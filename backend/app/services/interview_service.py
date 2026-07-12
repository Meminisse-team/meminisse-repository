"""
Phase 2: 유저 주도형 맞춤 인터뷰의 실시간 대화 루프.

여기서는 "경량 게이팅"만 수행한다(기획안 4절) — 답변마다 저비용 판별로 슬롯 충족
여부만 갱신하고, 그 결과로 다음 질문(꼬리 질문 여부)만 결정한다. 정밀 라벨 추출과
이벤트 분할은 세션 종료 후 event_extraction_service가 Celery 워커에서 수행한다.

DB 접근은 전부 app.gateways를 통한다 — 이 파일은 SQLAlchemy를 알지 못한다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.agents import prompts
from app.clients import solar
from app.gateways.dto import ChatLogRecord, InterviewSessionRecord, SessionCreateData
from app.gateways.factory import Gateways
from app.models.enums import MessageRole, UserStage
from app.schemas.interview import SessionCreate
from app.services import event_extraction_service


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

    # User.current_stage는 가입 시 ONBOARDING으로 고정된 뒤 어디서도 갱신되지
    # 않아 프로필 화면이 항상 "온보딩 중"으로만 표시되는 버그가 있었다(2026-07-12
    # 발견). 첫 인터뷰 세션이 만들어지는 시점이 곧 "대화 진행 중" 전환 시점이다.
    user = await gateways.users.get_by_id(user_id)
    if user is not None and user.current_stage == UserStage.ONBOARDING:
        await gateways.users.update(user_id, current_stage=UserStage.INTERVIEW)

    await gateways.commit()
    return session


async def get_session(gateways: Gateways, session_id: uuid.UUID) -> InterviewSessionRecord | None:
    return await gateways.sessions.get_by_id(session_id)


async def list_sessions(gateways: Gateways, user_id: uuid.UUID) -> list[InterviewSessionRecord]:
    """GET /interview-sessions(대시보드 '오늘의 대화'가 이어갈 세션을 찾거나,
    최근 세션 미리보기를 보여주는 데 사용). started_at 내림차순 — 가장 최근
    세션이 배열 맨 앞에 온다."""
    return await gateways.sessions.list_by_user(user_id)


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
    elif session.pending_ocr_confirmation_event_id is not None:
        # 직전 턴에서 낸 OCR 확인 질문("~가 맞으신가요?")에 대한 답 — 슬롯 게이팅
        # 대상이 아니라 이 하나의 사건 승격 여부를 결정하는 데만 쓰인다.
        assistant_content = await _resolve_ocr_confirmation_turn(gateways, session, content)
    elif await _detect_strong_negative_emotion(content):
        # 1층: 위기까지는 아니지만 심화 질문은 피해야 할 만큼 강한 부정적 감정 —
        # 슬롯/꼬리질문 진행 없이 완충 응답만 돌려주고 세션은 계속 열어 둔다(2층과
        # 달리 세션을 종료하지 않는다 — 사용자가 원하면 다른 이야기로 이어갈 수 있게).
        assistant_content = await _generate_tier1_buffer(content)
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
            # 이 사건에 대해서는 더 물을 게 없다 — 넘어가기 전에, 예전 사진/일기장
            # 업로드에서 OCR 오인식 의심으로 격리된(verified=false) 사건이 있으면
            # 그걸 먼저 확인 질문으로 낸다(TODO였던 승격 경로, 2026-07-12 연결).
            # 없으면 기존 자리표시자 문구 그대로.
            pending = await event_extraction_service.list_pending_ocr_confirmations(
                gateways, session.user_id
            )
            if pending:
                target = pending[0]
                assistant_content = prompts.build_ocr_confirmation_question(
                    suspected_text=(target.source_span or {}).get(
                        "quoted_text", target.one_line_summary
                    ),
                    guessed_value=target.one_line_summary,
                )
                await gateways.sessions.set_pending_ocr_confirmation(session.id, target.id)
            else:
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


async def _resolve_ocr_confirmation_turn(
    gateways: Gateways, session: InterviewSessionRecord, content: str
) -> str:
    confirmed = await _classify_ocr_confirmation_answer(content)
    await event_extraction_service.resolve_ocr_confirmation(
        gateways, session.pending_ocr_confirmation_event_id, confirmed=confirmed
    )
    await gateways.sessions.set_pending_ocr_confirmation(session.id, None)
    return (
        "확인해주셔서 감사해요! 편하게 다른 이야기도 들려주세요."
        if confirmed
        else "아, 제가 잘못 짚었나 봐요. 편하게 다른 이야기 들려주세요."
    )


async def _classify_ocr_confirmation_answer(content: str) -> bool:
    messages = prompts.build_ocr_confirmation_answer_prompt(latest_answer=content)
    result = await solar.structured_completion(
        messages,
        schema_name="ocr_confirmation_answer",
        json_schema=prompts.OCR_CONFIRMATION_ANSWER_SCHEMA,
        reasoning_effort="low",
    )
    return bool(result.get("confirmed", False))


async def _detect_strong_negative_emotion(content: str) -> bool:
    messages = prompts.build_tier1_detection_prompt(latest_answer=content)
    result = await solar.structured_completion(
        messages,
        schema_name="tier1_detection",
        json_schema=prompts.TIER1_DETECTION_SCHEMA,
        reasoning_effort="low",
    )
    return bool(result.get("strong_negative_emotion", False))


async def _generate_tier1_buffer(content: str) -> str:
    messages = prompts.build_tier1_buffer_prompt(latest_answer=content)
    response = await solar.chat_completion(messages, reasoning_effort="low", max_tokens=150)
    return response.choices[0].message.content or ""


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
    """세션을 종료 처리하고, Phase 2 후처리(산문 재조립 + 이벤트 추출)를 비동기로 예약한다.

    세션 상태 갱신(complete)이 이미 커밋된 뒤에 큐잉을 시도하므로, 브로커(Redis)가
    잠깐 응답하지 않더라도 "대화 종료" 자체는 사용자에게 성공으로 보여야 한다.
    Celery `.delay()`는 브로커에 동기적으로 연결을 시도하는 블로킹 호출이라
    `asyncio.to_thread`로 이벤트 루프 밖에서 돌린다 — 그렇지 않으면 브로커가 죽어있는
    동안 그 지연시간만큼 이 프로세스의 다른 모든 동시 요청이 함께 멎는다(실제 Supabase
    연동 검증 중 Redis 미기동 상태로 재현: /complete 호출 하나가 뒤이은 다른 사용자의
    요청까지 몇 초씩 지연시킴, 2026-07-11).
    """
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    from app.workers.tasks import process_session_completion  # 순환 임포트 방지용 지연 임포트

    try:
        await asyncio.to_thread(process_session_completion.delay, str(session.id))
    except Exception:
        logging.getLogger(__name__).warning(
            "process_session_completion 큐잉 실패 (session_id=%s) — 세션 자체는 이미 "
            "완료 처리됐으나 Phase 2 후처리가 예약되지 못했다.",
            session.id,
            exc_info=True,
        )

    updated_session = await gateways.sessions.get_by_id(session.id)
    assert updated_session is not None
    return updated_session
