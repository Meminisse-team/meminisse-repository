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
from datetime import datetime

from app.gateways.dto import InterviewSessionRecord
from app.gateways.factory import Gateways
from app.models.enums import MessageRole, SessionType

_SUBTITLE_SEPARATOR = " · "
_FALLBACK_TITLE = "이야기"


@dataclass
class StoryCard:
    session_id: uuid.UUID
    title: str
    subtitle: str | None
    prose: str
    completed_at: datetime | None


async def list_story_cards(gateways: Gateways, user_id: uuid.UUID) -> list[StoryCard]:
    """본인의 완료된 세션 중 산문 재조립이 끝난 것만, 최신순(started_at 내림차순,
    InterviewSessionGateway.list_by_user 정렬 계약 참조)으로 카드를 만들어 반환한다.
    아직 Phase 2 후처리(Celery)가 끝나지 않아 session_prose가 비어 있는 세션은
    보여줄 내용이 없으므로 건너뛴다 — 프론트가 폴링으로 자동 갱신한다
    (frontend/src/app/dashboard/stories/page.tsx)."""
    sessions = await gateways.sessions.list_by_user(user_id)
    cards: list[StoryCard] = []
    for session in sessions:
        if session.session_prose is None:
            continue

        title = await _resolve_title(gateways, session)

        events = await gateways.events.list_by_session(session.id)
        subtitle = (
            _SUBTITLE_SEPARATOR.join(event.one_line_summary for event in events) if events else None
        )

        cards.append(
            StoryCard(
                session_id=session.id,
                title=title,
                subtitle=subtitle,
                prose=session.session_prose,
                completed_at=session.completed_at,
            )
        )
    return cards


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
