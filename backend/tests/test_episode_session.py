"""
자유 에피소드(EPISODE) 세션 회귀 테스트(대시보드 "에피소드 추가", 2026-07-16).

핵심 계약: 사용자가 큐(고정 질문/사진)와 무관하게 직접 시작하는 세션이라 —
(1) 생성 시 고정 질문 큐를 소비/자동배정하지 않고,
(2) 오프닝 chat_log가 남아 "나의 이야기" 카드 제목으로 쓰이고,
(3) 다른 세션 타입과 동일하게 슬롯 충족 → 마무리 확인 → 완료 흐름을 그대로 따른다
    (interview_service.py:is_single_event_session에 EPISODE가 빠지면 이 계약이
    깨져 세션이 에러 없이 영원히 OPEN 상태로 남는다 — 이 파일의 핵심 회귀 가드).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.agents import prompts
from app.gateways.dto import UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import MessageRole, SessionStatus, SessionType
from app.schemas.interview import SessionCreate
from app.services import interview_service, story_service

_ALL_SLOTS_FILLED = dict.fromkeys(
    ["place", "time", "event", "emotion", "values", "companion"], True
)
_LONG_ENOUGH_CONTENT = "내용" * 45  # interview_service.MIN_RICH_ANSWER_LENGTH(80자)보다 길어야 함


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


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    if schema_name == "tier1_detection":
        return {"strong_negative_emotion": False}
    if schema_name == "slot_gating":
        return {"newly_filled_slots": []}
    if schema_name == "contextual_followup":
        return {"has_followup": False, "question": None}
    raise AssertionError(f"unexpected schema_name: {schema_name}")


def _patches():
    return (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        # complete_session()의 Celery 큐잉이 실제 브로커 연결을 시도하지 않게 모킹한다
        # (test_photo_session_orchestration.py와 동일한 이유).
        patch("app.workers.tasks.process_session_completion.delay"),
    )


async def _make_user(gateways):
    return await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )


@pytest.mark.asyncio
async def test_creating_episode_session_does_not_consume_question_queue() -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _make_user(gateways)

        next_before = await gateways.questions.get_next_unasked(user.id)
        assert next_before is not None

        session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.EPISODE)
        )

        assert session.session_type == SessionType.EPISODE
        assert session.question_id is None
        assert session.linked_media_asset_id is None

        next_after = await gateways.questions.get_next_unasked(user.id)
        assert next_after is not None
        assert next_after.id == next_before.id  # 큐가 그대로 — 에피소드가 질문을 소비하지 않았다


@pytest.mark.asyncio
async def test_episode_session_gets_opening_chat_log() -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _make_user(gateways)

        session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.EPISODE)
        )

        detail = await gateways.sessions.get_by_id(session.id)
        assert detail is not None
        assert len(detail.chat_logs) == 1
        assert detail.chat_logs[0].role == MessageRole.ASSISTANT
        assert detail.chat_logs[0].content == prompts.EPISODE_SESSION_OPENING


@pytest.mark.asyncio
async def test_episode_session_completes_via_add_user_turn() -> None:
    """is_single_event_session에 EPISODE가 빠지면 이 테스트가 실패한다 —
    슬롯을 다 채우고 두 턴을 보내도 세션이 COMPLETED로 전이하지 않고 계속 OPEN인
    채로 남기 때문이다."""
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _make_user(gateways)
        session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.EPISODE)
        )

        await gateways.sessions.update_slots(
            session.id, slots_filled=_ALL_SLOTS_FILLED, followup_count=0
        )
        session = await gateways.sessions.get_by_id(session.id)
        await interview_service.add_user_turn(gateways, session, _LONG_ENOUGH_CONTENT)

        session = await gateways.sessions.get_by_id(session.id)
        _, assistant_turn, updated = await interview_service.add_user_turn(
            gateways, session, "아니요, 없어요"
        )

        assert updated.status == SessionStatus.COMPLETED
        assert assistant_turn.content == "네, 잘 들었어요. 소중한 이야기 들려주셔서 감사해요."


@pytest.mark.asyncio
async def test_completed_episode_session_appears_as_story_card() -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _make_user(gateways)
        session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.EPISODE)
        )
        await gateways.sessions.set_session_prose(session.id, "직접 들려준 나만의 이야기.")
        await gateways.sessions.complete(session.id)
        await gateways.commit()

        cards = await story_service.list_story_cards(gateways, user.id)

        assert len(cards) == 1
        assert cards[0].title == prompts.EPISODE_SESSION_OPENING
        assert cards[0].prose == "직접 들려준 나만의 이야기."
        assert cards[0].is_generating is False
