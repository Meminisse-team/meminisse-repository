"""
Phase 2: 유저 주도형 맞춤 인터뷰의 실시간 대화 루프.

여기서는 "경량 게이팅"만 수행한다(기획안 4절) — 답변마다 저비용 판별로 슬롯 충족
여부만 갱신하고, 그 결과로 다음 질문(꼬리 질문 여부)만 결정한다. 정밀 라벨 추출과
이벤트 분할은 세션 종료 후 event_extraction_service가 Celery 워커에서 수행한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import prompts
from app.clients import solar
from app.models import ChatLog, InterviewSession, MessageRole, SessionStatus
from app.schemas.interview import SessionCreate


async def create_session(db: AsyncSession, payload: SessionCreate) -> InterviewSession:
    session = InterviewSession(
        user_id=payload.user_id,
        session_type=payload.session_type,
        question_id=payload.question_id,
        linked_media_asset_id=payload.linked_media_asset_id,
        slots_filled={key: False for key in prompts.ALL_SLOTS},
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> InterviewSession | None:
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .options(selectinload(InterviewSession.chat_logs))
    )
    return result.scalar_one_or_none()


async def _next_turn_index(session: InterviewSession) -> int:
    return len(session.chat_logs)


async def add_user_turn(
    db: AsyncSession, session: InterviewSession, content: str
) -> tuple[ChatLog, ChatLog]:
    """유저 발화를 저장하고, 세이프가드·슬롯 게이팅을 거쳐 에이전트 응답을 생성한다.

    반환값은 (user_chat_log, assistant_chat_log).
    """
    turn_index = await _next_turn_index(session)
    user_turn = ChatLog(
        session_id=session.id, role=MessageRole.USER, content=content, turn_index=turn_index,
    )
    db.add(user_turn)

    if prompts.contains_crisis_keyword(content):
        # 2층: 위기 신호 — 심화 질문 전면 차단, 세션을 부드럽게 마무리.
        assistant_content = prompts.TIER2_CRISIS_RESPONSE
        session.status = SessionStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc)
    else:
        newly_filled = await _run_slot_gating(content=content, slots_filled=session.slots_filled)
        session.slots_filled = {**session.slots_filled, **{slot: True for slot in newly_filled}}

        missing_required = [
            key for key in prompts.REQUIRED_SLOTS if not session.slots_filled.get(key)
        ]
        if missing_required and session.followup_count < prompts.MAX_FOLLOWUP_PER_EVENT:
            assistant_content = await _generate_followup_question(
                event_summary=content, missing_required_slots=missing_required,
                followup_count=session.followup_count,
            )
            session.followup_count += 1
        else:
            # TODO(향후 작업): 생애주기별 질문 큐/사진 핀셋 배치 오케스트레이션.
            # 지금은 이 사건에 대한 슬롯이 충분히 채워졌다는 것만 알리는 자리표시자.
            assistant_content = "말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"

    assistant_turn = ChatLog(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content=assistant_content,
        turn_index=turn_index + 1,
    )
    db.add(assistant_turn)

    await db.commit()
    await db.refresh(user_turn)
    await db.refresh(assistant_turn)
    await db.refresh(session)
    return user_turn, assistant_turn


async def _run_slot_gating(*, content: str, slots_filled: dict[str, bool]) -> list[str]:
    messages = prompts.build_slot_gating_prompt(latest_answer=content, slots_filled=slots_filled)
    result = await solar.structured_completion(
        messages,
        schema_name="slot_gating",
        json_schema=prompts.SLOT_GATING_SCHEMA,
        reasoning_effort="low",
    )
    return result.get("newly_filled_slots", [])


async def _generate_followup_question(
    *, event_summary: str, missing_required_slots: list[str], followup_count: int
) -> str:
    messages = prompts.build_followup_prompt(
        event_summary=event_summary,
        missing_required_slots=missing_required_slots,
        followup_count=followup_count,
    )
    response = await solar.chat_completion(messages, reasoning_effort="low", max_tokens=200)
    return response.choices[0].message.content or ""


async def complete_session(db: AsyncSession, session: InterviewSession) -> InterviewSession:
    """세션을 종료 처리하고, Phase 2 후처리(산문 재조립 + 이벤트 추출)를 비동기로 예약한다."""
    session.status = SessionStatus.COMPLETED
    session.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)

    from app.workers.tasks import process_session_completion  # 순환 임포트 방지용 지연 임포트

    process_session_completion.delay(str(session.id))
    return session
