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
from dataclasses import dataclass

from app.agents import prompts
from app.clients import solar
from app.gateways.dto import ChatLogRecord, InterviewSessionRecord, MediaAssetRecord, QuestionRecord, SessionCreateData
from app.gateways.factory import Gateways
from app.models.enums import LifePeriod, MessageRole, SessionType, UserStage
from app.schemas.interview import SessionCreate

# 유년기→청년기→장년기→노년기 — enum 선언 순서 그대로가 정본이다(app/models/enums.py).
_LIFE_PERIOD_ORDER = list(LifePeriod)


class NoRemainingQuestionsError(Exception):
    """FIXED_QUESTION 세션을 question_id 없이(=다음 항목을 자동으로 배정받도록)
    생성하려 했는데, 이 유저에게 더 배정할 고정 질문도 사진 세션도 없는 경우
    (전체 큐를 다 마침). 라우터가 이를 못 잡으면 500으로 새어나간다
    (app/api/v1/interviews.py 참조)."""


@dataclass
class _NextInterviewItem:
    """다음으로 진행할 세션 하나 — 고정 질문 또는 사진(docs/QUESTION_BANK_GUIDE.md 5절)."""

    session_type: SessionType
    question: QuestionRecord | None = None
    media_asset: MediaAssetRecord | None = None


async def _resolve_next_item(gateways: Gateways, user_id: uuid.UUID) -> _NextInterviewItem | None:
    """다음으로 보여줄 세션 하나를 고른다 — 고정 질문 큐와 사진 큐를 합쳐, 생애주기
    경계마다(그 시기 고정 질문을 모두 마친 직후) 그 시기 사진을 먼저 끼워 넣고,
    고정 질문을 전부 마치면 남은 시기별·시기 불명 사진을 순서대로 제시한다.

    상태를 별도로 저장하지 않는다 — "그 사진에 이미 PHOTO 세션이 있는지", "이
    생애주기 질문이 이미 하나라도 배정된 적 있는지"만으로 판정하므로 몇 번을 다시
    호출해도 같은 답을 준다(멱등) — 세션 완료 직후 미리보기 문구를 만들 때와
    실제로 다음 세션을 만들 때 둘 다 이 함수 하나로 처리할 수 있는 이유.

    "뒤늦게 업로드된 사진" 처리: 사진은 언제든 업로드할 수 있으므로, 이미 질문이
    끝난 지 한참 지난 생애주기의 사진이 나중에 들어올 수 있다. 이 경우 그 시기
    경계는 이미 지나갔으므로 지금 진행 중인 대화에 뒤늦게 끼어들지 않고, 시기
    불명 사진과 함께 전체 완료 후 몰아보기로 미룬다 — 그래서 아래 경계 확인은
    "next_question이 그 생애주기의 첫 질문이라 아직 한 번도 배정된 적 없을 때"
    (=지금 막 그 경계를 넘은 시점)에만 하고, 이미 그 생애주기 질문을 하나라도
    시작했으면(설령 next_question이 그 생애주기 중이라도) 건너뛴다."""
    next_question = await gateways.questions.get_next_unasked(user_id)

    if next_question is not None:
        idx = _LIFE_PERIOD_ORDER.index(next_question.life_period)
        if idx > 0:
            already_started = await gateways.questions.has_assigned_question_in_period(
                user_id, next_question.life_period
            )
            if not already_started:
                # 지금 막 이 생애주기로 넘어온 참이다 — 바로 앞 생애주기의 사진이
                # 아직 남아있으면 다음 고정 질문보다 먼저 보여준다.
                preceding_period = _LIFE_PERIOD_ORDER[idx - 1]
                photos = await gateways.media_assets.list_uninterviewed(
                    user_id, life_period=preceding_period
                )
                if photos:
                    return _NextInterviewItem(session_type=SessionType.PHOTO, media_asset=photos[0])
        return _NextInterviewItem(session_type=SessionType.FIXED_QUESTION, question=next_question)

    # 고정 질문을 모두 마쳤다 — 생애주기별로 혹시 남은 사진, 그다음 시기 불명 사진.
    for period in _LIFE_PERIOD_ORDER:
        photos = await gateways.media_assets.list_uninterviewed(user_id, life_period=period)
        if photos:
            return _NextInterviewItem(session_type=SessionType.PHOTO, media_asset=photos[0])
    photos = await gateways.media_assets.list_uninterviewed(user_id, life_period=None)
    if photos:
        return _NextInterviewItem(session_type=SessionType.PHOTO, media_asset=photos[0])

    return None


async def _photo_session_opening_text(gateways: Gateways, media_asset: MediaAssetRecord) -> str:
    pending_event = await gateways.events.get_pending_document_confirmation(media_asset.id)
    ocr_hint = (
        (pending_event.source_span or {}).get("quoted_text") if pending_event is not None else None
    )
    return prompts.build_photo_session_opening(ocr_suspected_text=ocr_hint)


async def create_session(
    gateways: Gateways, user_id: uuid.UUID, payload: SessionCreate
) -> InterviewSessionRecord:
    """user_id는 라우터가 인증된 current_user.id로부터 넘긴다(SessionCreate 스키마에는
    더 이상 user_id 필드가 없다 — app/schemas/interview.py 참조).

    FIXED_QUESTION 세션인데 question_id도 linked_media_asset_id도 안 넘어왔으면
    (프론트가 직접 고르지 않고 큐에 맡기는 일반적인 경로) _resolve_next_item으로
    다음 항목(고정 질문 또는 사진)을 자동 배정한다 — session_type이 그 결과에 따라
    PHOTO로 바뀔 수도 있다. 세션 종료 시점이 아니라 시작 시점에 고르는 이유는
    docs/QUESTION_BANK_GUIDE.md 4절 참조."""
    session_type = payload.session_type
    question_id = payload.question_id
    linked_media_asset_id = payload.linked_media_asset_id

    if (
        question_id is None
        and linked_media_asset_id is None
        and session_type == SessionType.FIXED_QUESTION
    ):
        next_item = await _resolve_next_item(gateways, user_id)
        if next_item is None:
            raise NoRemainingQuestionsError()
        session_type = next_item.session_type
        if next_item.session_type == SessionType.FIXED_QUESTION:
            assert next_item.question is not None
            question_id = next_item.question.id
        else:
            assert next_item.media_asset is not None
            linked_media_asset_id = next_item.media_asset.id

    session = await gateways.sessions.create(
        SessionCreateData(
            user_id=user_id,
            session_type=session_type,
            question_id=question_id,
            linked_media_asset_id=linked_media_asset_id,
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
    elif await _detect_strong_negative_emotion(content):
        # 1층: 위기까지는 아니지만 심화 질문은 피해야 할 만큼 강한 부정적 감정 —
        # 슬롯/꼬리질문 진행 없이 완충 응답만 돌려주고 세션은 계속 열어 둔다(2층과
        # 달리 세션을 종료하지 않는다 — 사용자가 원하면 다른 이야기로 이어갈 수 있게).
        assistant_content = await _generate_tier1_buffer(content)
    else:
        newly_filled = await _run_slot_gating(content=content, slots_filled=session.slots_filled)
        updated_slots = {**session.slots_filled, **{slot: True for slot in newly_filled}}
        missing_required = [key for key in prompts.REQUIRED_SLOTS if not updated_slots.get(key)]

        should_complete = False
        if missing_required and session.followup_count < prompts.MAX_FOLLOWUP_PER_EVENT:
            assistant_content = await _generate_followup_question(
                event_summary=content,
                missing_required_slots=missing_required,
                followup_count=session.followup_count,
            )
            new_followup_count = session.followup_count + 1
        else:
            new_followup_count = session.followup_count
            # PHOTO 세션도 FIXED_QUESTION과 동일하게 슬롯이 다 채워지면 완료 처리하고
            # 다음 항목을 제시한다(docs/QUESTION_BANK_GUIDE.md 5절 — "이후 대화는
            # 일반 인터뷰와 동일하게 진행된다").
            should_complete = session.session_type in (SessionType.FIXED_QUESTION, SessionType.PHOTO)
            assistant_content = "말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"

        await gateways.sessions.update_slots(
            session.id, slots_filled=updated_slots, followup_count=new_followup_count
        )

        if should_complete:
            if session.session_type == SessionType.PHOTO and session.linked_media_asset_id is not None:
                # 이 사진 세션의 대화로 정식 이벤트가 곧 추출될 것이므로(Phase 2
                # 후처리), 애초에 이 세션을 촉발한 OCR 스테이징 이벤트(오인식 의심,
                # verified=false)는 역할을 다했다 — 정리한다.
                pending_event = await gateways.events.get_pending_document_confirmation(
                    session.linked_media_asset_id
                )
                if pending_event is not None:
                    await gateways.events.delete(pending_event.id)

            # "한 세션 = 질문 하나" 관례(InterviewSession 모델 docstring)에 따라,
            # 이 세션의 슬롯이 충분히 채워졌으면 바로 완료 처리하고(Phase 2 후처리
            # 큐잉까지 포함 — complete_session 참조) 다음 항목을 미리 보여준다.
            # 프론트는 이 응답을 받은 뒤 새 세션을 만들어 이어가면 된다.
            await complete_session(gateways, session)
            next_item = await _resolve_next_item(gateways, session.user_id)
            if next_item is None:
                assistant_content = (
                    "말씀해주셔서 감사해요. 준비된 질문에 모두 답변해 주셨어요. 정말 수고 많으셨습니다."
                )
            elif next_item.session_type == SessionType.FIXED_QUESTION:
                assert next_item.question is not None
                assistant_content = (
                    f"말씀해주셔서 감사해요. 다음 질문으로 넘어가 볼까요?\n\n{next_item.question.content}"
                )
            else:
                assert next_item.media_asset is not None
                opening = await _photo_session_opening_text(gateways, next_item.media_asset)
                assistant_content = f"말씀해주셔서 감사해요. 이번엔 사진 속 이야기를 들어볼까요?\n\n{opening}"

    assistant_turn = await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content=assistant_content
    )
    await gateways.commit()

    updated_session = await gateways.sessions.get_by_id(session.id)
    assert updated_session is not None
    return user_turn, assistant_turn, updated_session


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
