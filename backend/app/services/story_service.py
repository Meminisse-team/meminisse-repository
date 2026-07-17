"""
'나의 이야기' 탭의 세션 단위 카드 조회.

기존에는 사건(Event) 단위로만 목록을 보여줘(event_service.py), 짧은 답변일수록
"무엇에 대한 이야기인지" 알기 어려웠다(2026-07-15 피드백 — 예: "서울대"만 봐서는
무슨 질문에 대한 답인지 모름). 이 서비스는 완료된 세션마다 하나의 카드로 묶어
- 제목: 그 세션이 다룬 질문/사진 오프닝 문구 그 자체(첫 chat_log, role=assistant —
  interview_service.py:_resolve_opening_content가 세션 생성 시점에 저장)
- 부제: 그 세션에서 재조립된 산문으로부터 재추출한 요약 라벨(Event.one_line_summary,
  여러 사건으로 쪼개졌으면 이어붙임)
- 본문: 재조립된 산문(session_prose) 그 자체
를 함께 보여준다.

호환성 참고: 첫 chat_log 자동 저장 기능이 추가되기 전(2026-07-15)에 만들어진
세션은 chat_logs[0]이 없거나 user 턴이다 — 그런 세션도 "나의 이야기"에서 통째로
사라지면 안 되므로(실사용 중 재현: session_prose가 있는 세션 3개가 전부 목록에서
빠짐), question_id로 질문을 다시 찾아보는 폴백을 거쳐도 못 찾으면 일반 라벨로
표시한다 — 카드 자체를 건너뛰지는 않는다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.gateways.dto import InterviewSessionRecord
from app.gateways.factory import Gateways
from app.models.enums import MessageRole, SessionType
from app.services import event_extraction_service

_SUBTITLE_SEPARATOR = " · "
_FALLBACK_TITLE = "이야기"

# 저장(반영) 버튼을 연타해도 이벤트 재추출(Solar 구조화 호출)이 그때마다 나가지
# 않도록 막는 쿨다운. 2026-07-15 검토 결과 건당 비용 자체는 작지만(세션 하나 분량
# 산문 재추출, 약 3.7천 토큰 실측), 조급한 연타로 인한 낭비까지 막을 필요는 있다고
# 판단해 최소한의 방어선만 둔다 — 신중하게 여러 번 고쳐 쓰는 정당한 사용까지
# 막으려는 목적은 아니다.
_PROSE_EDIT_COOLDOWN = timedelta(seconds=60)


class StoryNotFoundError(Exception):
    """세션이 없거나 본인 소유가 아니다."""


class ProseNotReadyError(Exception):
    """아직 Phase 2 후처리(산문 재조립)가 끝나지 않아 편집할 대상이 없다."""


class ProseEditCooldownError(Exception):
    """직전 편집 저장 이후 쿨다운이 끝나지 않았다."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{retry_after_seconds}초 후 다시 시도해주세요.")


@dataclass
class StoryCard:
    session_id: uuid.UUID
    title: str
    subtitle: str | None
    prose: str
    completed_at: datetime | None
    is_generating: bool = False


@dataclass
class StoryCardPage:
    """list_story_cards의 반환 봉투. items는 이 페이지 분량, total은 이 유저의
    완료된 세션 전체 개수(프론트 페이지 번호 UI의 총 페이지 수 계산용) —
    페이지네이션이 UI에서만 존재하고 실제 조회는 항상 전체를 가져오던 문제
    (2026-07-17 발견: 100개가 있어도 7개씩 보여주는 화면 체감 속도가 동일)의
    수정으로 도입됐다."""

    items: list[StoryCard]
    total: int


async def list_story_cards(
    gateways: Gateways, user_id: uuid.UUID, *, limit: int, offset: int
) -> StoryCardPage:
    """본인의 완료된 세션 중 이 페이지 분량만, 최신순(started_at 내림차순,
    InterviewSessionGateway.list_completed_by_user 정렬 계약 참조)으로 카드를
    만들어 반환한다. 상태 필터링(COMPLETED만)과 페이지네이션은 게이트웨이가
    SQL LIMIT/OFFSET으로 처리하므로, 여기서는 이 페이지에 해당하는 세션에
    대해서만 카드를 조립한다 — 세션이 아무리 많아도 매 호출의 추가 조회
    (_resolve_title/_to_story_card의 get_by_id·events 조회)는 최대 limit개로
    고정된다.

    완료(status=COMPLETED)됐지만 아직 Phase 2 후처리(Celery)가 안 끝나 session_prose가
    비어 있는 세션도 건너뛰지 않고 is_generating=True인 placeholder 카드로 포함한다 —
    예전엔 통째로 목록에서 빠져, 방금 나눈 대화가 정말 저장됐는지 사용자가 확인할
    길이 없었다(2026-07-16 피드백 — "생성 중" 임시 셀 요청). 프론트가 폴링으로
    자동 갱신하다가(frontend/src/app/dashboard/stories/page.tsx) prose가 채워지면
    이 placeholder를 완성된 카드로 자연스럽게 교체한다.

    아직 대화 중(OPEN)이거나 사용자에게 보여준 적도 없이 건너뛴(SKIPPED) 세션은
    "끝난 이야기"가 아니므로 제외한다(게이트웨이 쿼리 자체가 COMPLETED만 대상)."""
    sessions = await gateways.sessions.list_completed_by_user(user_id, limit=limit, offset=offset)
    total = await gateways.sessions.count_completed_by_user(user_id)
    cards: list[StoryCard] = []
    for session in sessions:
        if session.session_prose is None:
            cards.append(await _to_placeholder_card(gateways, session))
        else:
            cards.append(await _to_story_card(gateways, session))
    return StoryCardPage(items=cards, total=total)


async def update_session_prose(
    gateways: Gateways, user_id: uuid.UUID, session_id: uuid.UUID, new_prose: str
) -> StoryCard:
    """사용자가 '나의 이야기'에서 재조립된 산문이 마음에 들지 않을 때 직접 고쳐
    저장한다(저장 버튼을 눌렀을 때만 호출 — 타이핑마다 호출하지 않는다, 2026-07-15
    피드백). 저장 즉시 이 텍스트를 사람이 검수·확정한 것으로 간주해 왜곡 탐지 없이
    session_prose를 덮어쓰고, 이 세션의 이벤트를 새 텍스트 기준으로 재추출한다
    (event_extraction_service.reextract_events_from_edited_prose) — 그래야 최종
    원고 집필(write_chapter)이 참조하는 Event 테이블도 편집 내용을 반영한다."""
    session = await gateways.sessions.get_by_id(session_id)
    if session is None or session.user_id != user_id:
        raise StoryNotFoundError()
    if session.session_prose is None:
        raise ProseNotReadyError()

    now = datetime.now(timezone.utc)
    if session.prose_last_edited_at is not None:
        elapsed = now - session.prose_last_edited_at
        if elapsed < _PROSE_EDIT_COOLDOWN:
            remaining = _PROSE_EDIT_COOLDOWN - elapsed
            raise ProseEditCooldownError(retry_after_seconds=int(remaining.total_seconds()) + 1)

    await gateways.sessions.apply_user_prose_edit(session_id, new_prose=new_prose, edited_at=now)
    await gateways.commit()
    await event_extraction_service.reextract_events_from_edited_prose(gateways, session_id)

    updated_session = await gateways.sessions.get_by_id(session_id)
    return await _to_story_card(gateways, updated_session)


async def _to_story_card(gateways: Gateways, session: InterviewSessionRecord) -> StoryCard:
    title = await _resolve_title(gateways, session)
    events = await gateways.events.list_by_session(session.id)
    subtitle = (
        _SUBTITLE_SEPARATOR.join(event.one_line_summary for event in events) if events else None
    )
    return StoryCard(
        session_id=session.id,
        title=title,
        subtitle=subtitle,
        prose=session.session_prose,
        completed_at=session.completed_at,
        is_generating=False,
    )


async def _to_placeholder_card(gateways: Gateways, session: InterviewSessionRecord) -> StoryCard:
    """세션은 끝났지만(status=COMPLETED) 산문 재조립이 아직 안 끝난 경우의 임시 카드.
    제목은 chat_logs에 이미 저장돼 있어(세션 생성 시점) 정상적으로 보여줄 수 있지만,
    부제·본문은 Celery 작업이 끝나야 알 수 있으므로 비워둔다."""
    title = await _resolve_title(gateways, session)
    return StoryCard(
        session_id=session.id,
        title=title,
        subtitle=None,
        prose="",
        completed_at=session.completed_at,
        is_generating=True,
    )


async def _resolve_title(gateways: Gateways, session: InterviewSessionRecord) -> str:
    """이 세션의 카드 제목을 정한다 — 우선순위: (1) 첫 chat_log가 assistant 턴이면
    그 내용(정상 경로, 2026-07-15 이후 생성된 모든 세션), (2) FIXED_QUESTION이고
    question_id가 남아있으면 Question을 다시 찾아 그 문구(2026-07-15 이전에 만들어진
    구 세션 — 오프닝 chat_log가 없다), (3) 그래도 못 찾으면 일반 라벨. 카드를 통째로
    건너뛰지 않는 게 핵심 — 실사용 중 이 폴백이 없어 session_prose가 있는 세션이
    "나의 이야기"에서 전부 사라지는 회귀가 있었다."""
    detail = await gateways.sessions.get_by_id(session.id)
    if detail is not None and detail.chat_logs:
        opening_log = detail.chat_logs[0]
        if opening_log.role == MessageRole.ASSISTANT:
            return opening_log.content

    if session.session_type == SessionType.FIXED_QUESTION and session.question_id is not None:
        question = await gateways.questions.get_by_id(session.question_id)
        if question is not None:
            return question.content

    return _FALLBACK_TITLE
