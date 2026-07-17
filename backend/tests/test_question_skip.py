"""사용자 트리거 질문 건너뛰기(interview_service.skip_next_item / skip_session) 테스트.

핵심 계약(2026-07-16, '이 질문 넘어가기' 버튼): 사용자가 거부한 질문/사진은
SKIPPED 세션으로 배정 처리되어 다시는 후보에 오르지 않고(get_next_unasked·
list_uninterviewed의 큐 제외 계약), skip_session은 complete_session과 달리
Phase 2 후처리를 큐잉하지 않은 채 상태만 전이한다.
"""

from __future__ import annotations

import uuid

import pytest

from app.gateways.dto import MediaAssetCreateData, SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.gateways.mock.store import default_store
from app.models.enums import AssetType, LifePeriod, SessionStatus, SessionType
from app.schemas.interview import SessionCreate
from app.services import interview_service
from app.services.interview_service import NoRemainingQuestionsError, SessionNotOpenError


async def _make_user(gateways):
    return await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )


def _sessions_of(user_id):
    return [s for s in default_store.sessions.values() if s.user_id == user_id]


async def _complete_all_fixed_questions(gateways, user_id) -> None:
    """이 사용자의 고정 질문 큐를 전부 COMPLETED로 소진시켜, 다음 항목이 사진
    큐에서 나오게 만든다(test_dynamic_question_filtering의 헬퍼와 같은 방식)."""
    for q in default_store.questions.values():
        session = await gateways.sessions.create(
            SessionCreateData(
                user_id=user_id, session_type=SessionType.FIXED_QUESTION, question_id=q.id
            )
        )
        await gateways.sessions.complete(session.id)
    await gateways.commit()


@pytest.mark.asyncio
async def test_skip_next_item_marks_question_skipped_and_returns_following_preview() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)

    first = await interview_service._resolve_next_item(gateways, user.id)
    assert first is not None and first.question is not None

    preview = await interview_service.skip_next_item(gateways, user.id)

    # 건너뛴 질문은 SKIPPED 세션으로 배정 처리됐고, 사용자에게 보인 적이 없으니
    # chat_log도 없어야 한다(동적 질문 필터링의 자동 스킵과 동일한 형태).
    skipped = [s for s in _sessions_of(user.id) if s.status == SessionStatus.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].question_id == first.question.id
    assert skipped[0].chat_logs == []

    # 반환된 미리보기는 그다음 질문이어야 한다.
    following = await interview_service._resolve_next_item(gateways, user.id)
    assert following is not None and following.question is not None
    assert following.question.sequence_order > first.question.sequence_order
    assert preview.session_type == SessionType.FIXED_QUESTION
    assert following.question.content in preview.opening_message


@pytest.mark.asyncio
async def test_skipped_question_is_never_offered_again() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)

    first = await interview_service._resolve_next_item(gateways, user.id)
    assert first is not None and first.question is not None
    await interview_service.skip_next_item(gateways, user.id)

    offered_ids = set()
    for _ in range(3):
        item = await interview_service._resolve_next_item(gateways, user.id)
        assert item is not None and item.question is not None
        offered_ids.add(item.question.id)
    assert first.question.id not in offered_ids


@pytest.mark.asyncio
async def test_skip_next_item_skips_photo_session_too() -> None:
    """다음 항목이 사진 대화인 상태에서도 같은 버튼으로 건너뛸 수 있어야 한다 —
    건너뛴 사진은 SKIPPED PHOTO 세션이 달려 list_uninterviewed 계약에 따라
    다시 대화 후보에 오르지 않는다."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    await _complete_all_fixed_questions(gateways, user.id)
    photo = await gateways.media_assets.create(
        MediaAssetCreateData(
            user_id=user.id,
            s3_key="k",
            s3_url="https://example.com/k",
            asset_type=AssetType.IMAGE,
            life_period_mapped=LifePeriod.CHILDHOOD,
        )
    )
    await gateways.commit()

    next_item = await interview_service._resolve_next_item(gateways, user.id)
    assert next_item is not None and next_item.session_type == SessionType.PHOTO

    preview = await interview_service.skip_next_item(gateways, user.id)

    skipped = [
        s
        for s in _sessions_of(user.id)
        if s.status == SessionStatus.SKIPPED and s.session_type == SessionType.PHOTO
    ]
    assert len(skipped) == 1
    assert skipped[0].linked_media_asset_id == photo.id

    # 남은 항목이 없으니 "모두 답변" 미리보기가 오고, 그 사진은 다시 배정되지 않는다.
    assert preview.session_type is None
    assert await interview_service._resolve_next_item(gateways, user.id) is None


@pytest.mark.asyncio
async def test_skip_next_item_raises_when_queue_exhausted() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)

    # 이 사용자에게 남은 항목이 없어질 때까지 전부 건너뛴다(질문 + 사진 큐).
    while await interview_service._resolve_next_item(gateways, user.id) is not None:
        await interview_service.skip_next_item(gateways, user.id)

    with pytest.raises(NoRemainingQuestionsError):
        await interview_service.skip_next_item(gateways, user.id)


@pytest.mark.asyncio
async def test_skip_session_transitions_open_session_to_skipped() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await interview_service.create_session(
        gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
    )

    updated = await interview_service.skip_session(gateways, session)

    assert updated.status == SessionStatus.SKIPPED
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_skip_session_rejects_already_closed_session() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await interview_service.create_session(
        gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.complete(session.id)
    await gateways.commit()
    closed = await gateways.sessions.get_by_id(session.id)
    assert closed is not None

    with pytest.raises(SessionNotOpenError):
        await interview_service.skip_session(gateways, closed)
