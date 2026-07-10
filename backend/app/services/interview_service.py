"""
Phase 2: 유저 주도형 맞춤 인터뷰의 실시간 대화 루프.

여기서는 "경량 게이팅"만 수행한다(기획안 4절) — 답변마다 저비용 판별로 슬롯 충족
여부만 갱신하고, 그 결과로 다음 질문(꼬리 질문 여부)만 결정한다. 정밀 라벨 추출과
이벤트 분할은 세션 종료 후 event_extraction_service가 Celery 워커에서 수행한다.

DB 접근은 전부 app.gateways를 통한다 — 이 파일은 SQLAlchemy를 알지 못한다.
"""

from __future__ import annotations

import uuid

from app.agents import prompts
from app.clients import solar
from app.gateways.dto import ChatLogRecord, InterviewSessionRecord, SessionCreateData
from app.gateways.factory import Gateways
from app.models.enums import MessageRole
from app.schemas.interview import SessionCreate


async def create_session(
    gateways: Gateways, user_id: uuid.UUID, payload: SessionCreate
) -> InterviewSessionRecord:
    """user_id는 라우터가 인증된 current_user.id로부터 넘긴다(SessionCreate 스키마에는
    더 이상 user_id 필드가 없다 — app/schemas/interview.py 참조)."""
    session = await gateways.sessions.create(
        SessionCreateData(
            user_id=user_id,
            session_type=payload.session_type,
            question_id=payload.question_id,
            linked_media_asset_id=payload.linked_media_asset_id,
            initial_slots_filled={key: False for key in prompts.ALL_SLOTS},
        )
    )
    await gateways.commit()
    return session


async def get_session(gateways: Gateways, session_id: uuid.UUID) -> InterviewSessionRecord | None:
    return await gateways.sessions.get_by_id(session_id)


async def add_user_turn(
    gateways: Gateways, session: InterviewSessionRecord, content: str
) -> tuple[ChatLogRecord, ChatLogRecord, InterviewSessionRecord]:
    """유저 발화를 저장하고, 세이프가드·슬롯 게이팅을 거쳐 에이전트 응답을 생성한다.

    반환값은 (user_chat_log, assistant_chat_log, 갱신된 세션).
    """
    user_turn = await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.USER, content=content
    )

    if prompts.contains_crisis_keyword(content):
        # 2층: 위기 신호 — 심화 질문 전면 차단, 세션을 부드럽게 마무리.
        assistant_content = prompts.TIER2_CRISIS_RESPONSE
        await gateways.sessions.complete(session.id)
    else:
        newly_filled = await _run_slot_gating(content=content, slots_filled=session.slots_filled)
        updated_slots = {**session.slots_filled, **{slot: True for slot in newly_filled}}
        missing_required = [key for key in prompts.REQUIRED_SLOTS if not updated_slots.get(key)]

        if missing_required and session.followup_count < prompts.MAX_FOLLOWUP_PER_EVENT:
            assistant_content = await _generate_followup_question(
                event_summary=content,
                missing_required_slots=missing_required,
                followup_count=session.followup_count,
            )
            new_followup_count = session.followup_count + 1
        else:
            # TODO(향후 작업): 생애주기별 질문 큐/사진 핀셋 배치 오케스트레이션.
            # 지금은 이 사건에 대한 슬롯이 충분히 채워졌다는 것만 알리는 자리표시자.
            assistant_content = "말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"
            new_followup_count = session.followup_count

        await gateways.sessions.update_slots(
            session.id, slots_filled=updated_slots, followup_count=new_followup_count
        )

    assistant_turn = await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content=assistant_content
    )
    await gateways.commit()

    updated_session = await gateways.sessions.get_by_id(session.id)
    assert updated_session is not None
    return user_turn, assistant_turn, updated_session


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


async def complete_session(
    gateways: Gateways, session: InterviewSessionRecord
) -> InterviewSessionRecord:
    """세션을 종료 처리하고, Phase 2 후처리(산문 재조립 + 이벤트 추출)를 비동기로 예약한다."""
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    from app.workers.tasks import process_session_completion  # 순환 임포트 방지용 지연 임포트

    process_session_completion.delay(str(session.id))

    updated_session = await gateways.sessions.get_by_id(session.id)
    assert updated_session is not None
    return updated_session
