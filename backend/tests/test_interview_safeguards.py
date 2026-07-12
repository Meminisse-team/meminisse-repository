"""
P4 컴플라이언스 마감 회귀 테스트: interview_service.add_user_turn에 새로 연결한
1층(완충 응답) 세이프가드와 OCR 확인질문 승격 경로.

Solar 호출은 전부 모킹한다 — 프롬프트 품질이 아니라 배선(어떤 분기를 타고, 세션/
이벤트 상태가 어떻게 바뀌는지)을 검증하는 것이 목적이다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.gateways.dto import EventCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import EventSourceType, SessionType
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
    base = {"tier1_detection": {"strong_negative_emotion": False}, "slot_gating": {"newly_filled_slots": []}}
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
    # 슬롯 게이팅 분기를 건너뛰고 곧장 "다음 이야기로" 자리표시자(및 OCR 확인질문
    # 우선순위 체크) 분기를 타도록 필수 슬롯을 미리 다 채워 둔다.
    await gateways.sessions.update_slots(
        session.id,
        slots_filled=dict.fromkeys(
            ["place", "time", "event", "emotion", "values", "companion"], True
        ),
        followup_count=0,
    )
    return user, await gateways.sessions.get_by_id(session.id)


@pytest.mark.asyncio
async def test_tier1_buffer_skips_slot_gating_and_keeps_session_open() -> None:
    structured = _structured_responses(tier1_detection={"strong_negative_emotion": True})
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
async def test_ocr_confirmation_question_asked_then_confirmed_promotes_event() -> None:
    structured = _structured_responses()
    p1, p2, p3 = _patches(structured)
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user, session = await _new_session(gateways)

        event = await gateways.events.create(
            EventCreateData(
                user_id=user.id,
                source_type=EventSourceType.DOCUMENT,
                one_line_summary="1975년 결혼",
                prose_paragraph="1975년에 결혼했다는 기록.",
                source_span={"quoted_text": "1975년 결혼"},
                verified=False,
            )
        )
        await gateways.commit()

        _, assistant1, session = await interview_service.add_user_turn(
            gateways, session, "오늘은 이런 일이 있었어요."
        )
        assert "1975년 결혼" in assistant1.content
        assert session.pending_ocr_confirmation_event_id == event.id

        structured["ocr_confirmation_answer"] = {"confirmed": True}
        _, assistant2, session = await interview_service.add_user_turn(gateways, session, "네 맞아요")

        assert session.pending_ocr_confirmation_event_id is None
        confirmed = (await gateways.events.list_by_ids([event.id]))[0]
        assert confirmed.verified is True


@pytest.mark.asyncio
async def test_ocr_confirmation_denied_deletes_event() -> None:
    structured = _structured_responses()
    p1, p2, p3 = _patches(structured)
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user, session = await _new_session(gateways)

        event = await gateways.events.create(
            EventCreateData(
                user_id=user.id,
                source_type=EventSourceType.DOCUMENT,
                one_line_summary="오인식 의심 텍스트",
                prose_paragraph="오인식 의심 텍스트.",
                source_span={"quoted_text": "오인식 의심 텍스트"},
                verified=False,
            )
        )
        await gateways.commit()

        await interview_service.add_user_turn(gateways, session, "오늘은 이런 일이 있었어요.")
        session = await gateways.sessions.get_by_id(session.id)
        assert session.pending_ocr_confirmation_event_id == event.id

        structured["ocr_confirmation_answer"] = {"confirmed": False}
        await interview_service.add_user_turn(gateways, session, "아니요, 그런 적 없어요")

        assert (await gateways.events.list_by_ids([event.id])) == []
