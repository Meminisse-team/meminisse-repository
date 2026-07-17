"""
Phase 2: 유저 주도형 맞춤 인터뷰의 실시간 대화 루프.

여기서는 "경량 게이팅"만 수행한다(기획안 4절) — 답변마다 저비용 판별로 슬롯 충족
여부만 갱신하고, 그 결과로 다음 질문(꼬리 질문 여부)만 결정한다. 정밀 라벨 추출과
이벤트 분할은 세션 종료 후 event_extraction_service가 Celery 워커에서 수행한다.

DB 접근은 전부 app.gateways를 통한다 — 이 파일은 SQLAlchemy를 알지 못한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.agents import prompts
from app.clients import solar
from app.data.question_bank import QUESTION_BANK_BY_SEQUENCE
from app.gateways.dto import ChatLogRecord, InterviewSessionRecord, MediaAssetRecord, QuestionRecord, SessionCreateData, UserRecord
from app.gateways.factory import Gateways
from app.models.enums import LifePeriod, MessageRole, SessionStatus, SessionType, UserStage
from app.schemas.interview import SessionCreate

# 유년기→청년기→장년기→노년기 — enum 선언 순서 그대로가 정본이다(app/models/enums.py).
_LIFE_PERIOD_ORDER = list(LifePeriod)


class NoRemainingQuestionsError(Exception):
    """FIXED_QUESTION 세션을 question_id 없이(=다음 항목을 자동으로 배정받도록)
    생성하려 했는데, 이 유저에게 더 배정할 고정 질문도 사진 세션도 없는 경우
    (전체 큐를 다 마침). 라우터가 이를 못 잡으면 500으로 새어나간다
    (app/api/v1/interviews.py 참조)."""


class SessionNotOpenError(Exception):
    """이미 완료(또는 건너뜀)된 세션에 발화를 이어붙이려 한 경우. 프론트가 세션 완료
    시점에 새 세션으로 넘어가지 않고 같은 session_id로 계속 보내면, 이미 꽉 찬
    slots_filled 때문에 매 턴마다 "슬롯 충족" 분기가 재발동해 그 세션이 반복
    재완료 처리되고 Phase 2 후처리(session_prose 재조립·이벤트 추출)가 매번 다시
    돌아 사실상 매 턴마다 새 이벤트가 중복 생성되는 문제가 있었다(2026-07-14
    실제 프론트 사용 중 재현: "다음 세션으로 안 넘어가고 같은 질문 반복" +
    "대화가 세션별 산문이 아니라 턴마다 개별 저장된 것처럼 보임"). 이 경로를
    아예 막아 프론트가 반드시 새 세션을 만들도록 강제한다."""


@dataclass
class _NextInterviewItem:
    """다음으로 진행할 세션 하나 — 고정 질문 또는 사진(docs/QUESTION_BANK_GUIDE.md 5절)."""

    session_type: SessionType
    question: QuestionRecord | None = None
    media_asset: MediaAssetRecord | None = None


def _question_eligible(question: QuestionRecord, user: UserRecord) -> bool:
    """question_bank.py의 "eligibility" 메타데이터로 이 질문이 이 유저에게
    적합한지 판정한다. 조건이 없거나, 있어도 그 프로필 필드를 아직 모르면
    (None — 응답하지 않음) 항상 통과시킨다: 모르면 안전하게 묻는 쪽이 기본값이고,
    가입 시 명시적으로 입력받은 값과 명확히 어긋난다고 확인된 경우에만 건너뛴다
    (2026-07-16 설계 — 대화 내용 추론이 아니라 온보딩 라디오 버튼 응답 기준)."""
    entry = QUESTION_BANK_BY_SEQUENCE.get(question.sequence_order)
    eligibility = entry.get("eligibility") if entry else None
    if eligibility is None:
        return True
    user_value = getattr(user, eligibility["field"], None)
    if user_value is None:
        return True
    normalized = user_value.value if hasattr(user_value, "value") else user_value
    if "requires_one_of" in eligibility:
        return normalized in eligibility["requires_one_of"]
    if "requires" in eligibility:
        return normalized == eligibility["requires"]
    return True


async def _auto_skip_question(
    gateways: Gateways, *, user_id: uuid.UUID, question_id: uuid.UUID
) -> None:
    """사용자에게 한 번도 보여주지 않고(chat_log 없음) 곧바로 SKIPPED로 전이한다.
    이 함수가 preview_next_item(GET, 커밋 없음) 안에서도 호출될 수 있어, 스킵
    처리가 그 요청의 다른 부분과 무관하게 반드시 영속되도록 여기서 직접
    커밋한다 — 안 그러면 GET 요청 종료 시 롤백돼 매번 같은 후보를 다시
    건너뛰는 계산을 반복하게 된다."""
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user_id, session_type=SessionType.FIXED_QUESTION, question_id=question_id)
    )
    await gateways.sessions.skip(session.id)
    await gateways.commit()


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
    시작했으면(설령 next_question이 그 생애주기 중이라도) 건너뛴다.

    동적 질문 필터링(2026-07-16): get_next_unasked가 돌려준 후보가 이 유저의
    프로필(가입 시 라디오 버튼으로 입력받은 education_level/marital_status/
    has_children)과 맞지 않으면(_question_eligible 참조) 사용자에게 보여주지
    않고 바로 SKIPPED 세션을 만들어(_auto_skip_question) "배정됨" 처리한 뒤
    다음 후보를 다시 조회한다 — get_next_unasked는 status가 OPEN이 아닌 세션의
    question_id를 제외하므로 이 루프는 매번 다른 후보로 수렴한다."""
    user = await gateways.users.get_by_id(user_id)
    next_question = await gateways.questions.get_next_unasked(user_id)
    while (
        next_question is not None
        and user is not None
        and not _question_eligible(next_question, user)
    ):
        await _auto_skip_question(gateways, user_id=user_id, question_id=next_question.id)
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


def _photo_session_opening_text(media_asset: MediaAssetRecord) -> str:
    """media_asset에 이미 저장된 Azure Vision 분석 결과(캡션 + 사진 속 텍스트,
    media_service._run_dual_track_analysis 참조)를 그대로 오프닝 질문 재료로
    쓴다 — 별도 조회 없이 순수 문자열 조립이라 동기 함수다."""
    return prompts.build_photo_session_opening(
        image_caption=media_asset.image_caption, ocr_text=media_asset.image_ocr_text
    )


@dataclass
class NextItemPreview:
    """세션을 실제로 만들지 않고 "다음 세션을 시작하면 뭘 묻게 될지"만 미리 계산한
    결과. 세션은 여전히 첫 발화 시점에야 만들어진다(빈 세션이 계속 쌓이는 것을
    막기 위한 기존 설계, ChatOverlay.tsx 참조) — 그래서 대화창을 열자마자 사용자가
    아무것도 입력하기 전에 "다음 질문이 뭔지" 보여주려면, 세션 생성과 분리된
    이 미리보기 경로가 필요하다."""

    session_type: SessionType | None  # None이면 배정할 항목이 없다는 뜻(질문·사진 큐를 모두 마침).
    linked_media_asset_id: uuid.UUID | None
    opening_message: str


async def preview_next_item(gateways: Gateways, user_id: uuid.UUID) -> NextItemPreview:
    """GET /interview-sessions/next-preview. 간단한 인사 + 아직 다루지 않은 다음
    질문(또는 사진 대화 시작 문구)을 합쳐 반환한다 — "어떤 대화를 해볼까요?" 같은
    정적 문구 대신 실제로 무엇을 물을지 대화창을 열자마자 보여주기 위함
    (2026-07-14 프론트 실사용 중 발견)."""
    next_item = await _resolve_next_item(gateways, user_id)
    if next_item is None:
        return NextItemPreview(
            session_type=None,
            linked_media_asset_id=None,
            opening_message=(
                "안녕하세요! 준비된 질문에는 모두 답변해 주셨어요. "
                "더 나누고 싶은 이야기가 있다면 편하게 말씀해주세요."
            ),
        )
    if next_item.session_type == SessionType.FIXED_QUESTION:
        assert next_item.question is not None
        return NextItemPreview(
            session_type=SessionType.FIXED_QUESTION,
            linked_media_asset_id=None,
            opening_message=f"안녕하세요! 오늘은 이 이야기를 들려주시겠어요?\n\n{next_item.question.content}",
        )

    assert next_item.media_asset is not None
    opening = _photo_session_opening_text(next_item.media_asset)
    return NextItemPreview(
        session_type=SessionType.PHOTO,
        linked_media_asset_id=next_item.media_asset.id,
        opening_message=f"안녕하세요! 이번엔 사진 속 이야기를 들어볼까요?\n\n{opening}",
    )


async def _resolve_opening_content(gateways: Gateways, session: InterviewSessionRecord) -> str | None:
    """세션이 다루는 질문/사진의 실제 문구를 조회한다 — create_session이 이를
    chat_log(role=assistant)로 남겨, 세션 종료 후 산문 재조립 시 "무엇에 대한
    답인지" 맥락이 함께 보존되게 한다(2026-07-15). 이전에는 이 문구가 프론트
    로컬 상태(previewNext 응답)로만 보여지고 실제로는 저장되지 않아, 예를 들어
    "대학을 어디 다녔나요?"라는 질문에 "서울대"라고만 답해도 DB에는 "서울대"
    한 마디만 남아 무엇에 대한 답인지 알 수 없는 문제가 있었다."""
    if session.session_type == SessionType.EPISODE:
        return prompts.EPISODE_SESSION_OPENING
    if session.session_type == SessionType.FIXED_QUESTION and session.question_id is not None:
        question = await gateways.questions.get_by_id(session.question_id)
        return question.content if question is not None else None
    if session.session_type == SessionType.PHOTO and session.linked_media_asset_id is not None:
        media_asset = await gateways.media_assets.get_by_id(session.linked_media_asset_id)
        if media_asset is None:
            return None
        return _photo_session_opening_text(media_asset)
    return None


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

    opening_content = await _resolve_opening_content(gateways, session)
    if opening_content is not None:
        await gateways.sessions.add_chat_log(
            session.id, role=MessageRole.ASSISTANT, content=opening_content
        )

    # User.current_stage는 가입 시 ONBOARDING으로 고정된 뒤 어디서도 갱신되지
    # 않아 프로필 화면이 항상 "온보딩 중"으로만 표시되는 버그가 있었다(2026-07-12
    # 발견). 첫 인터뷰 세션이 만들어지는 시점이 곧 "대화 진행 중" 전환 시점이다.
    user = await gateways.users.get_by_id(user_id)
    if user is not None and user.current_stage == UserStage.ONBOARDING:
        await gateways.users.update(user_id, current_stage=UserStage.INTERVIEW)

    await gateways.commit()
    return session


async def skip_next_item(gateways: Gateways, user_id: uuid.UUID) -> NextItemPreview:
    """POST /interview-sessions/skip-next — 아직 세션을 만들지 않은 미리보기 상태에서
    사용자가 '이 질문 넘어가기'를 누른 경우. 미리보기가 쓰는 것과 같은
    _resolve_next_item(멱등)으로 지금 화면에 보이는 항목을 다시 계산해, 그 항목의
    SKIPPED 세션을 만들어 "배정됨" 처리한다 — 고정 질문은 get_next_unasked 계약
    (비-OPEN 세션의 question_id 제외), 사진은 list_uninterviewed 계약(PHOTO 세션이
    달린 사진 제외)에 따라 다시는 후보에 오르지 않는다.

    건너뛴 직후의 새 미리보기를 함께 반환한다 — 프론트가 별도 왕복 없이 다음
    질문을 바로 보여줄 수 있게. 더 건너뛸 항목이 없으면 NoRemainingQuestionsError."""
    next_item = await _resolve_next_item(gateways, user_id)
    if next_item is None:
        raise NoRemainingQuestionsError()
    session = await gateways.sessions.create(
        SessionCreateData(
            user_id=user_id,
            session_type=next_item.session_type,
            question_id=next_item.question.id if next_item.question is not None else None,
            linked_media_asset_id=(
                next_item.media_asset.id if next_item.media_asset is not None else None
            ),
        )
    )
    await gateways.sessions.skip(session.id)
    await gateways.commit()
    return await preview_next_item(gateways, user_id)


async def skip_session(
    gateways: Gateways, session: InterviewSessionRecord
) -> InterviewSessionRecord:
    """POST /interview-sessions/{id}/skip — 이미 열린 세션의 질문을 사용자가 거부한
    경우. complete_session과 달리 Phase 2 후처리(산문 재조립·이벤트 추출)를 큐잉하지
    않는다 — 거부한 질문의 대화는 자서전 재료가 아니며, SKIPPED 세션은 '나의 이야기'
    목록에서도 제외된다(story_service 참조). 같은 질문/사진은 큐 제외 계약에 따라
    다시 배정되지 않는다."""
    if session.status != SessionStatus.OPEN:
        raise SessionNotOpenError()
    await gateways.sessions.skip(session.id)
    await gateways.commit()
    updated_session = await gateways.sessions.get_by_id(session.id)
    assert updated_session is not None
    return updated_session


async def get_session(gateways: Gateways, session_id: uuid.UUID) -> InterviewSessionRecord | None:
    return await gateways.sessions.get_by_id(session_id)


async def list_sessions(gateways: Gateways, user_id: uuid.UUID) -> list[InterviewSessionRecord]:
    """GET /interview-sessions(대시보드 '오늘의 대화'가 이어갈 세션을 찾거나,
    최근 세션 미리보기를 보여주는 데 사용). started_at 내림차순 — 가장 최근
    세션이 배열 맨 앞에 온다."""
    return await gateways.sessions.list_by_user(user_id)


_WRAP_UP_OFFERED_KEY = "_wrap_up_offered"  # slots_filled 안에 두는 내부 플래그들. 실제
_CONTEXTUAL_FOLLOWUP_OFFERED_KEY = "_contextual_followup_offered"  # 슬롯이 아니므로
# prompts.REQUIRED_SLOTS/ALL_SLOTS에는 없고, missing_required 판정에도 섞이지 않는다.


async def add_user_turn(
    gateways: Gateways, session: InterviewSessionRecord, content: str
) -> tuple[ChatLogRecord, ChatLogRecord, InterviewSessionRecord]:
    """유저 발화를 저장하고, 세이프가드·슬롯 게이팅을 거쳐 에이전트 응답을 생성한다.

    반환값은 (user_chat_log, assistant_chat_log, 갱신된 세션).

    Solar 호출(최대 90초, app/clients/base.py 참조)을 전부 끝낸 뒤에야 DB에 쓴다 —
    먼저 유저 발화부터 저장해 트랜잭션을 열어둔 채 느린 Solar 응답을 기다리면,
    Supabase가 idle-in-transaction 상태의 커넥션을 일정 시간 뒤 강제로 끊어 맨
    마지막 커밋이 실패하는 문제가 있었다(2026-07-15 실사용 중 재현 — 답변이 길어
    Solar 판정이 오래 걸리거나, 한 턴 안에서 Solar 호출이 여러 번 겹칠수록
    재현 빈도가 높았다). 그래서 이 함수는 두 단계로 나뉜다: (1) Solar 호출을
    포함한 순수 판단(DB 쓰기 없음), (2) 판단 결과를 바탕으로 몰아서 하는 빠른
    DB 쓰기.
    """
    if session.status != SessionStatus.OPEN:
        raise SessionNotOpenError()

    # --- 1단계: 판단(Solar 호출 포함) — DB 쓰기는 아직 하지 않는다 -------------
    updated_slots: dict[str, bool] | None = None
    new_followup_count = session.followup_count
    should_complete = False
    is_crisis = False

    if prompts.contains_crisis_keyword(content):
        # 2층: 위기 신호 — 심화 질문 전면 차단, 세션을 부드럽게 마무리. 키워드
        # 매칭이라 Solar 호출이 없다(app/agents/prompts.py:CRISIS_KEYWORDS).
        assistant_content = prompts.TIER2_CRISIS_RESPONSE
        is_crisis = True
    elif await _detect_strong_negative_emotion(content):
        # 1층: 위기까지는 아니지만 심화 질문은 피해야 할 만큼 강한 부정적 감정 —
        # 슬롯/꼬리질문 진행 없이 완충 응답만 돌려주고 세션은 계속 열어 둔다(2층과
        # 달리 세션을 종료하지 않는다 — 사용자가 원하면 다른 이야기로 이어갈 수 있게).
        assistant_content = await _generate_tier1_buffer(content)
    else:
        newly_filled = await _run_slot_gating(content=content, slots_filled=session.slots_filled)
        updated_slots = {**session.slots_filled, **{slot: True for slot in newly_filled}}
        missing_required = [key for key in prompts.REQUIRED_SLOTS if not updated_slots.get(key)]
        # "한 세션 = 사건 하나" 관례를 따르는 세션 타입 전부 — 슬롯 충족 후 마무리
        # 확인을 거쳐 자동 완료되고, 맥락 기반 꼬리질문도 이 타입들에서만 시도한다.
        # EPISODE(사용자가 직접 시작한 자유 에피소드, 2026-07-16)도 같은 관례를
        # 따른다 — 여기 빠지면 세션이 에러 없이 영원히 OPEN 상태로 남는다.
        is_single_event_session = session.session_type in (
            SessionType.FIXED_QUESTION, SessionType.PHOTO, SessionType.EPISODE,
        )

        def _finalize_wrap_up_or_complete() -> str:
            """슬롯·풍부함·맥락 기반 꼬리질문까지 다 거친 뒤 도달하는 마지막 갈림길
            — 마무리 확인을 아직 안 했으면 그것부터, 이미 했으면 진짜로 완료한다.
            둘 이상의 분기(맥락 꼬리질문이 "없음"으로 나온 경우와, 애초에 그 단계
            자체가 스킵된 경우)에서 공통으로 이 지점에 도달하므로 헬퍼로 뺐다."""
            nonlocal should_complete
            if not updated_slots.get(_WRAP_UP_OFFERED_KEY):
                updated_slots[_WRAP_UP_OFFERED_KEY] = True
                return prompts.WRAP_UP_CHECK_IN_MESSAGE
            # PHOTO 세션도 FIXED_QUESTION과 동일하게 다룬다(docs/QUESTION_BANK_
            # GUIDE.md 5절 — "이후 대화는 일반 인터뷰와 동일하게 진행된다"). 다음
            # 질문 미리보기는 더 이상 여기서 만들지 않는다 — 프론트가 "다음 이야기
            # 계속하기" 버튼을 누르는 시점에 GET next-preview로 새로 가져간다
            # (2026-07-15 — 이전엔 여기서 미리 다음 질문을 만들어 보여줘, 세션이
            # 끝나도 새 채팅이 열리는 느낌 없이 한 세션 안에서 여러 질문을 받는
            # 것처럼 보인다는 피드백이 있었다).
            should_complete = is_single_event_session
            return (
                "네, 잘 들었어요. 소중한 이야기 들려주셔서 감사해요."
                if should_complete
                else "말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"
            )

        if missing_required and session.followup_count < prompts.MAX_FOLLOWUP_PER_EVENT:
            assistant_content = await _generate_followup_question(
                event_summary=content,
                missing_required_slots=missing_required,
                followup_count=session.followup_count,
            )
            new_followup_count = session.followup_count + 1
        elif (
            _total_user_content_length(session, content) < prompts.MIN_RICH_ANSWER_LENGTH
            and session.followup_count < prompts.MAX_FOLLOWUP_PER_EVENT
        ):
            # 필수 슬롯은 다 찼지만 이 사건에 대해 실제로 쓴 글자 수가 너무 적다 —
            # 채팅 말풍선 UI가 카카오톡처럼 짧은 대답을 유도한다는 피드백(2026-07-14)
            # 대응. 남은 꼬리 질문 예산 안에서(MAX_FOLLOWUP_PER_EVENT 공유) 한 번 더
            # 자연스러운 구체화 질문을 던진다.
            assistant_content = await _generate_elaboration_question(content)
            new_followup_count = session.followup_count + 1
        elif (
            not updated_slots.get(_CONTEXTUAL_FOLLOWUP_OFFERED_KEY)
            and session.followup_count < prompts.MAX_FOLLOWUP_PER_EVENT
            and is_single_event_session
        ):
            # 슬롯(필수 정보)과 풍부함(길이)은 기계적 기준이었다 — 여기서는 그와
            # 별개로, 진짜 전기 작가라면 자연스럽게 캐물었을 만한 지점이 대화 속에
            # 있는지 LLM이 직접 판단한다(2026-07-15 피드백, INTERVIEW_PERSONA_
            # SYSTEM_PROMPT가 원래 표방했지만 실제로는 연결된 적 없던 역할). 세션당
            # 한 번만 시도하고(플래그로 기록), 꼬리 질문 예산을 공유해 무한정
            # 캐묻지 않는다. "캐물을 게 없다"는 결과가 나오면 같은 턴 안에서 바로
            # 마무리 확인으로 넘어간다 — 빈 라운드트립으로 한 턴을 낭비하지 않는다.
            updated_slots[_CONTEXTUAL_FOLLOWUP_OFFERED_KEY] = True
            contextual_question = await _generate_contextual_followup(session=session, latest_content=content)
            if contextual_question is not None:
                assistant_content = contextual_question
                new_followup_count = session.followup_count + 1
            else:
                assistant_content = _finalize_wrap_up_or_complete()
        else:
            assistant_content = _finalize_wrap_up_or_complete()

    # --- 2단계: 판단 결과를 몰아서 쓰는 DB 쓰기(빠름, Solar 호출 없음) ---------
    user_turn = await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.USER, content=content
    )

    if updated_slots is not None:
        await gateways.sessions.update_slots(
            session.id, slots_filled=updated_slots, followup_count=new_followup_count
        )

    if is_crisis:
        await gateways.sessions.complete(session.id)
    elif should_complete:
        # PHOTO 세션이 촉발되기 전 Azure Vision이 사진 속 텍스트에서 이미 만들어둔
        # Event(source_type=DOCUMENT)가 있어도 따로 정리하지 않는다 — 이 대화에서
        # 비슷한 사실이 다시 추출되면 Phase 3 중복 병합(_merge_duplicate_events)이
        # 임베딩 유사도 + LLM 판정으로 자연스럽게 흡수하므로, 별도의 "대기 중 삭제"
        # 로직을 둘 필요가 없다(media_service.py 모듈 docstring 참조).

        # "한 세션 = 질문 하나" 관례(InterviewSession 모델 docstring)에 따라, 이
        # 세션의 슬롯이 충분히 채워졌으면 바로 완료 처리한다(Phase 2 후처리 큐잉
        # 포함 — complete_session 참조). 프론트는 "다음 이야기 계속하기" 버튼을
        # 누르면 새 세션을 만들어 이어간다.
        await complete_session(gateways, session)

    assistant_turn = await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content=assistant_content
    )
    await gateways.commit()

    if is_crisis:
        # 위기 신호로 자동 종료된 세션도 Phase 2 후처리(산문 재조립·이벤트 추출)를
        # 정상 종료(complete_session)와 동일하게 큐잉한다(2026-07-16 해소 — 이전엔
        # 상태만 completed로 바꿔서, /complete를 따로 호출하지 않는 한 그 세션의
        # 이야기가 '나의 이야기'에 영영 "생성 중" placeholder로 남았다). 큐잉은
        # 위 커밋 이후여야 워커가 방금 저장된 대화까지 볼 수 있다. complete_session을
        # 그대로 쓰지 않는 이유: 그 함수는 자체적으로 상태 전이+커밋을 다시 하는데,
        # 이 함수는 위기 대응 문구(assistant_turn)까지 한 트랜잭션에 담아야 해서
        # 커밋 순서가 다르다.
        from app.workers.enqueue import enqueue_in_background
        from app.workers.tasks import process_session_completion  # 순환 임포트 방지용 지연 임포트

        enqueue_in_background(
            process_session_completion, str(session.id), log_context=f"session_id={session.id}"
        )

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


def _total_user_content_length(session: InterviewSessionRecord, latest_content: str) -> int:
    """이 세션에서 지금까지(이번 발화 포함) 사용자가 실제로 쓴 글자 수 총합 —
    "한 세션 = 사건 하나" 관례상 이 세션의 모든 사용자 턴이 같은 사건을 다룬다.
    session.chat_logs는 이번 발화 이전까지의 턴만 담고 있으므로(add_user_turn이
    아직 이번 턴을 세션에 반영하기 전) latest_content를 더해줘야 한다."""
    prior = sum(len(log.content) for log in session.chat_logs if log.role == MessageRole.USER)
    return prior + len(latest_content)


async def _generate_elaboration_question(content: str) -> str:
    messages = prompts.build_elaboration_prompt(user_content=content)
    response = await solar.chat_completion(messages, reasoning_effort="low", max_tokens=200)
    return response.choices[0].message.content or ""


async def _generate_contextual_followup(
    *, session: InterviewSessionRecord, latest_content: str
) -> str | None:
    """이 세션의 지금까지 대화 전체(오프닝 질문 포함, session.chat_logs)에 방금
    답변을 더해 LLM에게 넘기고, 자연스럽게 캐물을 지점이 있는지 판단시킨다.
    없다고 판단되면 None을 반환한다."""
    chat_turns = [{"role": log.role.value, "content": log.content} for log in session.chat_logs]
    chat_turns.append({"role": "user", "content": latest_content})
    result = await solar.structured_completion(
        prompts.build_contextual_followup_prompt(chat_turns=chat_turns),
        schema_name="contextual_followup",
        json_schema=prompts.CONTEXTUAL_FOLLOWUP_SCHEMA,
        reasoning_effort="low",
    )
    if not result.get("has_followup"):
        return None
    return result.get("question")


async def complete_session(
    gateways: Gateways, session: InterviewSessionRecord
) -> InterviewSessionRecord:
    """세션을 종료 처리하고, Phase 2 후처리(산문 재조립 + 이벤트 추출)를 비동기로 예약한다.

    세션 상태 갱신(complete)이 이미 커밋된 뒤에 큐잉을 시도하므로, 브로커(Redis)가
    잠깐 응답하지 않더라도 "대화 종료" 자체는 사용자에게 성공으로 보여야 한다.
    큐잉은 요청/응답 흐름과 분리해서(enqueue_in_background) 실행한다 — 이 함수를
    기다리지 않고 바로 반환하므로, 큐잉 재시도의 백오프 대기가 HTTP 응답 지연으로
    이어지지 않는다(2026-07-16, "세션 종료 시 긴 로딩" 문제의 원인 중 하나였음).
    큐잉이 그래도 실패하면 주기적 재조정 태스크가 나중에 다시 찾아 큐잉한다
    (app/workers/enqueue.py 모듈 docstring 참조 — "나의 이야기" 산문이 영구
    유실되던 사고의 아키텍처 레벨 해결)."""
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    from app.workers.enqueue import enqueue_in_background
    from app.workers.tasks import process_session_completion  # 순환 임포트 방지용 지연 임포트

    enqueue_in_background(
        process_session_completion, str(session.id), log_context=f"session_id={session.id}"
    )

    updated_session = await gateways.sessions.get_by_id(session.id)
    assert updated_session is not None
    return updated_session
