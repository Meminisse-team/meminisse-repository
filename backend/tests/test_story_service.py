"""'나의 이야기' 세션 카드 조회(story_service.py) 테스트.

핵심 계약: 카드 제목은 그 세션이 다룬 질문/사진 오프닝 문구 그 자체(첫 chat_log),
부제는 그 세션에서 재조립된 산문으로부터 재추출한 이벤트 요약(여러 개면 이어붙임).
아직 대화 중(OPEN)이거나 보여준 적 없이 건너뛴(SKIPPED) 세션은 목록에서 빠지지만,
완료(COMPLETED)됐는데 산문 재조립만 안 끝난 세션은 is_generating=True인 placeholder
카드로 나타난다(2026-07-16 — "생성 중" 임시 셀 요청, 이전엔 이 경우도 통째로 빠졌음).
"""

from __future__ import annotations

import uuid

import pytest

from app.gateways.dto import EventCreateData, SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.gateways.mock.store import default_store
from app.models.enums import EventSourceType, MessageRole, SessionType
from app.services import story_service


async def _make_user(gateways):
    return await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )


@pytest.mark.asyncio
async def test_story_card_uses_opening_question_as_title_and_event_summary_as_subtitle() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content="대학을 어디 다녔나요?"
    )
    await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content="서울대")
    await gateways.sessions.set_session_prose(session.id, "나는 서울대학교에 다녔다.")
    await gateways.sessions.complete(session.id)
    await gateways.events.create(
        EventCreateData(
            user_id=user.id,
            source_type=EventSourceType.SESSION_CHAT,
            session_id=session.id,
            one_line_summary="화자는 서울대학교에 다녔다.",
            prose_paragraph="나는 서울대학교에 다녔다.",
            verified=True,
        )
    )
    await gateways.commit()

    cards = await story_service.list_story_cards(gateways, user.id)

    assert len(cards) == 1
    card = cards[0]
    assert card.title == "대학을 어디 다녔나요?"
    assert card.subtitle == "화자는 서울대학교에 다녔다."
    assert card.prose == "나는 서울대학교에 다녔다."
    assert card.is_generating is False


@pytest.mark.asyncio
async def test_story_card_joins_multiple_event_summaries() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content="학창 시절 이야기를 들려주세요"
    )
    await gateways.sessions.set_session_prose(session.id, "전학을 갔고, 그 학교에서 친구를 사귀었다.")
    await gateways.sessions.complete(session.id)
    await gateways.events.bulk_create(
        [
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT, session_id=session.id,
                one_line_summary="전학", prose_paragraph="전학을 갔다.", verified=True,
            ),
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT, session_id=session.id,
                one_line_summary="새 친구를 사귐", prose_paragraph="친구를 사귀었다.", verified=True,
            ),
        ]
    )
    await gateways.commit()

    cards = await story_service.list_story_cards(gateways, user.id)

    assert cards[0].subtitle == "전학 · 새 친구를 사귐"


@pytest.mark.asyncio
async def test_story_card_shows_prose_without_subtitle_when_no_events_extracted() -> None:
    """왜곡 탐지에 걸려 이벤트 추출까지는 못 갔어도, 산문 재조립 자체는 끝났으면
    카드는 보여준다(본문=산문, 부제만 비어 있음)."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content="질문 내용"
    )
    await gateways.sessions.set_session_prose(session.id, "재조립된 산문.")
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    cards = await story_service.list_story_cards(gateways, user.id)

    assert len(cards) == 1
    assert cards[0].subtitle is None
    assert cards[0].prose == "재조립된 산문."


@pytest.mark.asyncio
async def test_legacy_session_without_opening_chat_log_falls_back_to_question_content() -> None:
    """오프닝 chat_log 자동 저장 기능(2026-07-15)이 생기기 전에 만들어진 세션은
    chat_logs[0]이 user 턴이거나 아예 없다 — 그런 세션도 question_id가 남아있으면
    그 질문 문구를 다시 찾아 제목으로 쓰고, 카드 자체는 절대 사라지면 안 된다
    (실사용 중 재현: session_prose가 있는 구 세션 전체가 목록에서 사라짐)."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    question = next(iter(default_store.questions.values()))
    session = await gateways.sessions.create(
        SessionCreateData(
            user_id=user.id, session_type=SessionType.FIXED_QUESTION, question_id=question.id
        )
    )
    # 오프닝 chat_log 없이(구 세션 재현) 바로 유저 발화부터 시작한다.
    await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content="답변")
    await gateways.sessions.set_session_prose(session.id, "답변 산문.")
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    cards = await story_service.list_story_cards(gateways, user.id)

    assert len(cards) == 1
    assert cards[0].title == question.content


@pytest.mark.asyncio
async def test_legacy_session_without_question_id_falls_back_to_generic_title() -> None:
    """question_id마저 없는 아주 오래된 세션도 카드가 사라지면 안 되고, 대신
    일반 라벨로라도 표시돼야 한다."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content="답변")
    await gateways.sessions.set_session_prose(session.id, "답변 산문.")
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    cards = await story_service.list_story_cards(gateways, user.id)

    assert len(cards) == 1
    assert cards[0].title == "이야기"


@pytest.mark.asyncio
async def test_open_sessions_are_excluded() -> None:
    """아직 대화 중(status=OPEN)인 세션은 "끝난 이야기"가 아니므로 목록에서 빠진다."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content="질문 내용"
    )
    await gateways.commit()

    cards = await story_service.list_story_cards(gateways, user.id)

    assert cards == []


@pytest.mark.asyncio
async def test_completed_session_without_prose_yet_shows_as_generating_placeholder() -> None:
    """2026-07-16: 완료(status=COMPLETED)됐지만 Phase 2 후처리(Celery)가 아직 안
    끝나 session_prose가 비어 있는 세션은 더 이상 목록에서 통째로 빠지지 않고,
    is_generating=True인 placeholder 카드로 나타난다 — 제목은 이미 알 수 있으니
    보여주고, 본문·부제는 비워둔다."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content="질문 내용"
    )
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    cards = await story_service.list_story_cards(gateways, user.id)

    assert len(cards) == 1
    card = cards[0]
    assert card.is_generating is True
    assert card.title == "질문 내용"
    assert card.subtitle is None
    assert card.prose == ""


@pytest.mark.asyncio
async def test_story_cards_are_scoped_to_the_requesting_user() -> None:
    gateways = _build_mock_gateways()
    a = await _make_user(gateways)
    b = await _make_user(gateways)
    for user in (a, b):
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
        )
        await gateways.sessions.add_chat_log(session.id, role=MessageRole.ASSISTANT, content="질문")
        await gateways.sessions.set_session_prose(session.id, "산문")
        await gateways.sessions.complete(session.id)
    await gateways.commit()

    a_cards = await story_service.list_story_cards(gateways, a.id)
    assert len(a_cards) == 1
