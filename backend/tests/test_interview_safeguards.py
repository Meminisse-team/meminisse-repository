"""
P4 컴플라이언스 마감 회귀 테스트: interview_service.add_user_turn에 새로 연결한
1층(완충 응답) 세이프가드.

Solar 호출은 전부 모킹한다 — 프롬프트 품질이 아니라 배선(어떤 분기를 타고, 세션
상태가 어떻게 바뀌는지)을 검증하는 것이 목적이다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.gateways.dto import UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.gateways.mock.store import default_store
from app.models.enums import SessionType
from app.schemas.interview import SessionCreate
from app.services import interview_service


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
    return _FakeCompletion("자유 텍스트 응답")


def _structured_responses(**overrides) -> dict:
    # 감정 판정과 슬롯 게이팅은 단일 turn_gating 호출로 통합됐다(2026-07-18,
    # interview_service._run_turn_gating).
    base = {"turn_gating": {"strong_negative_emotion": False, "newly_filled_slots": []}}
    base.update(overrides)
    return base


def _patches(structured: dict):
    async def fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return structured[schema_name]

    return (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=fake_structured_completion),
        patch("app.clients.embeddings.embed_passages", return_value=[[1.0, 0.0]]),
    )


async def _new_session(gateways):
    user = await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )
    await gateways.commit()
    session = await interview_service.create_session(
        gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
    )
    return user, session


@pytest.mark.asyncio
async def test_tier1_buffer_skips_slot_gating_and_keeps_session_open() -> None:
    structured = _structured_responses(
        turn_gating={"strong_negative_emotion": True, "newly_filled_slots": []}
    )
    p1, p2, p3 = _patches(structured)
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        _, session = await _new_session(gateways)
        prev_followup = session.followup_count

        _, assistant_turn, updated = await interview_service.add_user_turn(
            gateways, session, "그때 이야기는 너무 힘들어서 하고 싶지 않아요."
        )

        assert assistant_turn.content == "자유 텍스트 응답"  # TIER1_BUFFER_SYSTEM_PROMPT 응답 그대로
        assert updated.followup_count == prev_followup  # 슬롯 게이팅을 타지 않았으므로 변화 없음
        assert updated.status.value == "open"  # 2층과 달리 세션을 종료하지 않는다


@pytest.mark.asyncio
async def test_reflective_question_skips_slot_followup_even_with_no_filled_slots() -> None:
    """sequence_order 97~100(성찰·메시지·자유 발언형 질문)은 필수 슬롯이 하나도
    안 채워진 것으로 판정돼도(newly_filled_slots=[]) 슬롯 꼬리질문을 건너뛰어야
    한다(2026-07-20 — 시연 중 "이미 가족 이야기를 했는데 또 누구와 함께였는지
    캐묻는" 부자연스러운 재질문이 실제로 재현됨). 맥락 기반 꼬리질문도 "없음"으로
    나오면 곧장 마무리 확인으로 넘어가야 한다 — 그 사이의 분량부족형(elaboration)에
    걸리지 않도록 답변을 80자 이상으로 충분히 길게 준다."""
    question_100 = next(q for q in default_store.questions.values() if q.sequence_order == 100)

    structured = _structured_responses(
        turn_gating={"strong_negative_emotion": False, "newly_filled_slots": []},
        contextual_followup={"has_followup": False, "question": None},
    )

    async def _fail_if_slot_followup_called(*args, **kwargs):
        raise AssertionError("성찰형 질문인데도 슬롯 기반 꼬리질문(_generate_followup_question)이 호출됐다.")

    p1, p2, p3 = _patches(structured)
    with p1, p2, p3, patch(
        "app.services.interview_service._generate_followup_question",
        new=_fail_if_slot_followup_called,
    ):
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        await gateways.commit()
        session = await interview_service.create_session(
            gateways,
            user.id,
            SessionCreate(session_type=SessionType.FIXED_QUESTION, question_id=question_100.id),
        )

        long_answer = "저는 제 손에 대한 이야기를 남기고 싶습니다. " * 5  # 80자 이상
        _, assistant_turn, updated = await interview_service.add_user_turn(
            gateways, session, long_answer
        )

        # 슬롯 꼬리질문도, 분량부족형도 안 탔으니(맥락 꼬리질문도 "없음") 곧장 마무리 확인.
        from app.agents import prompts as agent_prompts

        assert assistant_turn.content == agent_prompts.WRAP_UP_CHECK_IN_MESSAGE
        assert updated.followup_count == 0
