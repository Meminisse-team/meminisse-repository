"""'나의 이야기' 산문 사용자 편집(story_service.update_session_prose) 테스트.

핵심 계약: 저장 즉시 사람이 검수·확정한 텍스트로 간주해 왜곡 탐지(NLI) 없이
session_prose를 덮어쓰고, 이 세션의 이벤트를 새 텍스트 기준으로 재추출한다.
원본은 최초 편집 시점에만 백업되고, 연타 저장은 쿨다운(429 상당)으로 막힌다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.gateways.dto import EventCreateData, SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import EventSourceType, MessageRole, SessionType
from app.services import story_service


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


async def _make_user(gateways):
    return await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )


async def _make_completed_session(gateways, user_id, *, prose: str):
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user_id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(session.id, role=MessageRole.ASSISTANT, content="질문 내용")
    await gateways.sessions.set_session_prose(session.id, prose)
    await gateways.sessions.complete(session.id)
    await gateways.events.create(
        EventCreateData(
            user_id=user_id,
            source_type=EventSourceType.SESSION_CHAT,
            session_id=session.id,
            one_line_summary="원본 AI 추출 이벤트",
            prose_paragraph=prose,
            verified=True,
        )
    )
    await gateways.commit()
    return session


def _patch_extraction_pipeline(*, summary: str = "새로 추출된 이벤트"):
    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        assert schema_name == "event_extraction"
        return {
            "events": [
                {
                    "one_line_summary": summary,
                    "prose_paragraph": "재추출된 문단",
                    "source_quote": "재추출된 문단",
                }
            ],
            "relations": [],
        }

    async def _fake_embed_passages(texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def _fail_if_called_entailment(*, premise: str, hypotheses: list[str]):
        raise AssertionError("사용자가 직접 편집한 텍스트는 왜곡 탐지(NLI)를 거치면 안 된다")

    return (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_passages", new=_fake_embed_passages),
        patch("app.clients.nli.classify_entailment_batch", new=_fail_if_called_entailment),
    )


@pytest.mark.asyncio
async def test_update_session_prose_overwrites_prose_and_backs_up_original() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await _make_completed_session(gateways, user.id, prose="AI가 재조립한 원본 산문.")

    p1, p2, p3 = _patch_extraction_pipeline()
    with p1, p2, p3:
        card = await story_service.update_session_prose(
            gateways, user.id, session.id, "사용자가 직접 고친 산문."
        )

    assert card.prose == "사용자가 직접 고친 산문."
    updated = await gateways.sessions.get_by_id(session.id)
    assert updated.session_prose == "사용자가 직접 고친 산문."
    assert updated.session_prose_original == "AI가 재조립한 원본 산문."


@pytest.mark.asyncio
async def test_update_session_prose_replaces_events_not_appends() -> None:
    """기존에 AI가 추출했던 이벤트는 폐기되고, 편집된 텍스트로 새로 추출한
    이벤트만 남아야 한다 — 둘 다 남으면 '나의 이야기' 부제가 중복된다."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await _make_completed_session(gateways, user.id, prose="원본")

    before = await gateways.events.list_by_session(session.id)
    assert len(before) == 1
    assert before[0].one_line_summary == "원본 AI 추출 이벤트"

    p1, p2, p3 = _patch_extraction_pipeline(summary="편집 후 재추출된 이벤트")
    with p1, p2, p3:
        await story_service.update_session_prose(gateways, user.id, session.id, "편집된 산문")

    after = await gateways.events.list_by_session(session.id)
    assert len(after) == 1
    assert after[0].one_line_summary == "편집 후 재추출된 이벤트"


@pytest.mark.asyncio
async def test_second_edit_does_not_overwrite_backed_up_original() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await _make_completed_session(gateways, user.id, prose="AI 원본")

    p1, p2, p3 = _patch_extraction_pipeline()
    with p1, p2, p3:
        await story_service.update_session_prose(gateways, user.id, session.id, "1차 편집")
        # 쿨다운을 우회하기 위해 편집 시각을 강제로 과거로 되돌린다(2차 편집 검증 목적).
        stored = await gateways.sessions.get_by_id(session.id)
        stored.prose_last_edited_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await story_service.update_session_prose(gateways, user.id, session.id, "2차 편집")

    final = await gateways.sessions.get_by_id(session.id)
    assert final.session_prose == "2차 편집"
    assert final.session_prose_original == "AI 원본"  # 최초 편집 시점 원본 그대로 유지


@pytest.mark.asyncio
async def test_update_session_prose_within_cooldown_raises() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await _make_completed_session(gateways, user.id, prose="원본")

    p1, p2, p3 = _patch_extraction_pipeline()
    with p1, p2, p3:
        await story_service.update_session_prose(gateways, user.id, session.id, "1차 편집")
        with pytest.raises(story_service.ProseEditCooldownError):
            await story_service.update_session_prose(gateways, user.id, session.id, "연타 편집")


@pytest.mark.asyncio
async def test_update_session_prose_rejects_other_users_session() -> None:
    gateways = _build_mock_gateways()
    owner = await _make_user(gateways)
    intruder = await _make_user(gateways)
    session = await _make_completed_session(gateways, owner.id, prose="원본")

    with pytest.raises(story_service.StoryNotFoundError):
        await story_service.update_session_prose(gateways, intruder.id, session.id, "남의 산문 수정 시도")


@pytest.mark.asyncio
async def test_update_session_prose_before_processing_finishes_raises() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.commit()  # session_prose가 아직 None (Phase 2 후처리 전)

    with pytest.raises(story_service.ProseNotReadyError):
        await story_service.update_session_prose(gateways, user.id, session.id, "아직 처리 안 된 세션")
