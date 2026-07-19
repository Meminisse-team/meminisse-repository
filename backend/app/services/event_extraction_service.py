"""
Phase 2 후처리 (세션 종료 즉시, Celery 비동기): 산문 재조립 → 왜곡 탐지 → 이벤트 분할·
라벨 추출 → 임베딩. app/workers/tasks.py의 Celery 태스크가 이 서비스를 호출한다.

SESSION_CHAT 출처 이벤트는 사용자가 인터뷰에서 직접 발화한 내용이므로, 재조립본이
왜곡 탐지를 통과하면 곧바로 verified=true로 저장한다. DOCUMENT 출처(사진 속 텍스트,
Azure Vision) 이벤트의 verified는 이 서비스가 아니라 media_service._run_dual_track_
analysis가 생성 시점에 1차 타당성 검증(_check_ocr_validity) 결과로 바로 정한다 — 별도
"확인 질문을 거쳐 승격"하는 단계는 없다(그런 방식을 대화 중간에 예/아니오로 끼워 넣는
잘못된 설계로 한 번 구현했다가 롤백한 이력이 있다, docs/QUESTION_BANK_GUIDE.md 5절
참조, 2026-07-12). 그 사진/문서는 별도의 PHOTO 세션 주제가 되어, 오프닝 질문에
캡션·텍스트를 자연스러운 실마리로 녹여 넣는 방식으로만 다뤄진다.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid

from app.agents import prompts
from app.clients import embeddings, nli, solar
from app.gateways.dto import EventCreateData, EventRecord, EventRelationCreateData
from app.gateways.factory import Gateways
from app.models.enums import EventSourceType

# 이보다 짧은 source_quote는 assistant 발화와 우연히 부분 문자열이 겹칠 위험이 커서
# (예: "네", "그렇군요") 인터뷰어 발화 유출 판정에서 제외한다.
_MIN_INTERVIEWER_LEAK_QUOTE_LENGTH = 4


def _log_timing(session_id: uuid.UUID, step: str, elapsed: float) -> None:
    """세션 47722ddc 등에서 반복되던 TimeoutError의 병목 구간(재조립/왜곡탐지/
    이벤트추출/임베딩 중 어디인지)을 확인하기 위한 임시 계측. 원인이 확인되면
    제거할 것(2026-07-19)."""
    print(f"    [timing] session={session_id} {step}: {elapsed:.1f}s", file=sys.stderr)


async def process_completed_session(gateways: Gateways, session_id: uuid.UUID) -> list[EventRecord]:
    session = await gateways.sessions.get_by_id(session_id)
    if session is None:
        raise ValueError(f"InterviewSession {session_id} not found")

    if session.session_prose is not None:
        # 멱등성 가드: admin_service.reconcile_stale_sessions(5분마다 실행)는 "완료됐는데
        # 아직 산문이 없는 세션"을 무조건 다시 큐잉한다 — 원래는 브로커 유실을 복구하기
        # 위한 안전망이지만, 처리 대기열이 밀린 상황(대량 시딩 등)에서는 "아직 처리 순서를
        # 못 받았을 뿐인 세션"까지 똑같이 다시 큐잉해버린다. 그 결과 같은 세션이 여러 번
        # 큐에 들어가 워커가 프롬프트 재조립·이벤트 추출을 통째로 반복 실행하면서, 시간을
        # 낭비하고 이벤트가 중복 생성되는 사고가 실사용 중 확인됐다(2026-07-16, 세션 12개에서
        # 중복 이벤트 재현). 이 함수는 세션당 한 번만 의미가 있으므로, 이미 산문이 있으면
        # (재조립이 이미 끝났다는 뜻) 아무것도 다시 하지 않고 기존 이벤트만 반환한다.
        # --pool=solo(app/workers/celery_app.py)라 태스크가 절대 동시 실행되지 않으므로
        # 이 확인-후-처리 사이에 경쟁 상태가 끼어들 여지가 없다.
        return await gateways.events.list_by_session(session_id)

    _session_t0 = time.monotonic()
    chat_turns = [{"role": log.role.value, "content": log.content} for log in session.chat_logs]
    reassembly_turns = _exclude_wrap_up_exchange(chat_turns)
    # "low"에서는 세션 안에 사건이 2개 이상이고 각각 후속 질문 병합이 필요할 때
    # 원본 문장과 병합 문장을 중복으로 남기는 오류가 실사용 검증(4회 중 약 2~3회)
    # 재현됐다. "medium"으로 올리면 같은 검증에서 대부분 정상 병합되고(4회 중
    # 3회 완전 정상), "high"는 오히려 21세·집에서 같은 구체적 사실을 뭉뚱그려
    # 누락시키는 별도 실패 유형이 나타나 1차 시도로는 채택하지 않았다(아래 재시도
    # 참조 — 왜곡 판정에 걸린 경우에 한해서만 high로 한 번 더 시도한다).
    _t0 = time.monotonic()
    session_prose = await _reassemble_prose(
        chat_turns=chat_turns, reassembly_turns=reassembly_turns, reasoning_effort="medium"
    )
    _log_timing(session_id, "reassemble(medium)", time.monotonic() - _t0)

    _t0 = time.monotonic()
    passed = await _passes_distortion_check(
        original_turns=chat_turns, reassembled_prose=session_prose
    )
    _log_timing(session_id, "distortion_check", time.monotonic() - _t0)
    if not passed:
        # 왜곡 임계값 초과: 같은 재료로 1회 재시도한다(reasoning_effort="high" —
        # 1차 시도의 medium과 다른 설정이라 같은 실패를 반복할 확률이 낮다).
        # 재시도본이 검증을 통과하면 그걸 채택하고, 그래도 실패하면 산문은
        # 저장하되 이벤트 추출을 보류하고 세션에 distortion_flagged를 남긴다 —
        # '나의 이야기' 카드가 "원문과 다를 수 있어요" 배지로 노출하고, 사용자가
        # 산문을 직접 확인·수정해 저장하면(사람이 확정한 텍스트) 플래그가 해제되며
        # 이벤트가 정상 추출된다(story_service.update_session_prose 경로,
        # 2026-07-18 — 오랜 TODO "조용한 스킵"의 해소).
        _t0 = time.monotonic()
        retry_prose = await _reassemble_prose(
            chat_turns=chat_turns, reassembly_turns=reassembly_turns, reasoning_effort="high"
        )
        _log_timing(session_id, "reassemble(high, retry)", time.monotonic() - _t0)

        _t0 = time.monotonic()
        retry_passed = await _passes_distortion_check(original_turns=chat_turns, reassembled_prose=retry_prose)
        _log_timing(session_id, "distortion_check(retry)", time.monotonic() - _t0)
        if retry_passed:
            session_prose = retry_prose
            passed = True
        else:
            logging.getLogger(__name__).warning(
                "세션 %s 산문 재조립이 왜곡 탐지를 재시도 후에도 통과하지 못해 "
                "이벤트 추출을 보류하고 플래그를 남긴다.",
                session_id,
            )

    await gateways.sessions.set_session_prose(session_id, session_prose)
    if not passed:
        await gateways.sessions.set_distortion_flagged(session_id, True)
        await gateways.commit()
        _log_timing(session_id, "TOTAL(distortion-flagged, no events)", time.monotonic() - _session_t0)
        return []

    # 세션의 첫 chat_log는 이 세션이 다룬 질문/사진 오프닝 문구다(role=assistant,
    # interview_service.py:_resolve_opening_content가 생성 시점에 저장). session_prose
    # 자체는 화자의 말만 최소 변형으로 담아야 하므로(왜곡 탐지 대상이라 손대면 안 됨,
    # _strip_leaked_assistant_sentences 참조) 이 맥락은 산문에 섞지 않고 이벤트 추출
    # 단계에만 별도로 건네 준다 — "서울대"처럼 답변이 짧아도 무엇에 대한 이야기인지
    # one_line_summary/prose_paragraph가 명확하게 나오도록(2026-07-15 피드백).
    question_context = _question_context(chat_turns)
    _t0 = time.monotonic()
    events = await _extract_events_from_prose(
        gateways,
        session=session,
        session_prose=session_prose,
        question_context=question_context,
        chat_turns=chat_turns,
    )
    _log_timing(session_id, "extract_events_from_prose(total)", time.monotonic() - _t0)

    await gateways.commit()
    _log_timing(session_id, "TOTAL", time.monotonic() - _session_t0)
    return events


async def reextract_events_from_edited_prose(
    gateways: Gateways, session_id: uuid.UUID
) -> list[EventRecord]:
    """사용자가 '나의 이야기'에서 재조립된 산문을 직접 고쳐 저장한 뒤 호출된다
    (story_service.update_session_prose). 이미 사람이 검수·확정한 텍스트이므로
    process_completed_session과 달리 산문 재조립도, 왜곡 탐지(NLI)도 다시 거치지
    않고 session.session_prose를 그대로 이벤트 추출의 입력으로 삼는다 — "왜곡"이라는
    개념 자체가 AI 생성물에만 적용되는 것이지 사용자 본인이 직접 쓴 텍스트에는
    적용될 수 없기 때문이다. 기존에 이 세션에서 추출됐던 이벤트는 전부 폐기하고
    새로 추출한 이벤트로 완전히 교체한다(부분 재사용 없음 — verified 승격 등 상태를
    이벤트별로 따로 판단하기보다 통째로 다시 만드는 편이 일관성이 단순하다)."""
    session = await gateways.sessions.get_by_id(session_id)
    if session is None:
        raise ValueError(f"InterviewSession {session_id} not found")
    if session.session_prose is None:
        raise ValueError(f"InterviewSession {session_id} has no session_prose yet")

    chat_turns = [{"role": log.role.value, "content": log.content} for log in session.chat_logs]
    await gateways.events.delete_by_session(session_id)
    events = await _extract_events_from_prose(
        gateways,
        session=session,
        session_prose=session.session_prose,
        question_context=_question_context(chat_turns),
        chat_turns=chat_turns,
    )
    await gateways.commit()
    return events


def _question_context(chat_turns: list[dict[str, str]]) -> str | None:
    return chat_turns[0]["content"] if chat_turns and chat_turns[0]["role"] == "assistant" else None


async def _reassemble_prose(
    *,
    chat_turns: list[dict[str, str]],
    reassembly_turns: list[dict[str, str]],
    reasoning_effort: str,
) -> str:
    """산문 재조립 1회 실행(Solar 호출 + 인터뷰어 발화 유출 제거). 왜곡 탐지
    실패 시 다른 reasoning_effort로 재시도할 수 있게 헬퍼로 분리했다."""
    prose_response = await solar.chat_completion(
        prompts.build_prose_reassembly_prompt(chat_turns=reassembly_turns),
        reasoning_effort=reasoning_effort,
    )
    return _strip_leaked_assistant_sentences(
        prose=prose_response.choices[0].message.content or "", chat_turns=chat_turns
    )


async def _extract_events_from_prose(
    gateways: Gateways,
    *,
    session,
    session_prose: str,
    question_context: str | None,
    chat_turns: list[dict[str, str]],
) -> list[EventRecord]:
    _t0 = time.monotonic()
    extraction = await solar.structured_completion(
        prompts.build_event_extraction_prompt(
            session_prose=session_prose, question_context=question_context
        ),
        schema_name="event_extraction",
        json_schema=prompts.EVENT_EXTRACTION_SCHEMA,
        reasoning_effort="medium",
    )
    _log_timing(session.id, "solar.structured_completion(event_extraction)", time.monotonic() - _t0)

    extracted, index_map = _filter_interviewer_leakage(
        extracted=extraction.get("events", []), chat_turns=chat_turns
    )
    _t0 = time.monotonic()
    events = await _persist_events(
        gateways,
        user_id=session.user_id,
        session_id=session.id,
        extracted=extracted,
        # 세션 단위 '꼭 넣기' 표시를 이벤트로 상속 — 토글이 추출보다 먼저 일어난
        # 세션(재추출 포함)에서 플래그가 유실되지 않게 한다(2026-07-18).
        is_must_include=session.is_must_include,
    )
    _log_timing(session.id, "persist_events(incl. embeddings)", time.monotonic() - _t0)
    await _persist_relations(
        gateways, events=events, relations=extraction.get("relations", []), index_map=index_map
    )
    return events


def _exclude_wrap_up_exchange(chat_turns: list[dict[str, str]]) -> list[dict[str, str]]:
    """마무리 확인 질문(WRAP_UP_CHECK_IN_MESSAGE)과 그에 대한 사용자 응답("넘어가자",
    "없어요" 등)은 이 사건에 대한 서술이 아니라 순수한 대화 진행 신호이므로, 산문
    재조립에 넘기기 전에 코드 레벨에서 통째로 제외한다 — PROSE_REASSEMBLY_SYSTEM_
    PROMPT의 "진행 신호는 옮기지 말라"는 지시만으로는 실제로 "다음으로 넘어가자"가
    산문 문장에 그대로 새어 들어가는 사고가 실사용 대화에서 재현됐다(2026-07-16).
    WRAP_UP_CHECK_IN_MESSAGE는 세션당 한 번, 고정 문구로만 등장하므로(interview_
    service.py:_finalize_wrap_up_or_complete) 문자열 완전 일치로 안전하게 찾아낼 수
    있다 — _strip_leaked_assistant_sentences와 같은 "프롬프트만으론 못 미더우니
    코드로 한 번 더 막는다"는 이 파일의 기존 패턴을 따른다."""
    filtered: list[dict[str, str]] = []
    skip_next_user_turn = False
    for turn in chat_turns:
        if skip_next_user_turn and turn.get("role") == "user":
            skip_next_user_turn = False
            continue
        if turn.get("role") == "assistant" and turn.get("content") == prompts.WRAP_UP_CHECK_IN_MESSAGE:
            skip_next_user_turn = True
            continue
        filtered.append(turn)
    return filtered


def _strip_leaked_assistant_sentences(*, prose: str, chat_turns: list[dict[str, str]]) -> str:
    """산문 재조립 단계의 코드 레벨 backstop — _filter_interviewer_leakage와 같은
    발상을 한 단계 앞(이벤트 추출이 아니라 session_prose 자체)에 적용한다.

    PROSE_REASSEMBLY_SYSTEM_PROMPT가 "assistant 턴은 산문에 포함하지 말라"고
    명시하지만, 세션 완료 시 마지막 assistant 턴에 다음 질문 전체 문장이 그대로
    담기게 되면서(interview_service.py:add_user_turn, "다음 질문으로 넘어가
    볼까요?\\n\\n{content}") LLM이 이를 사용자 발화로 착각해 산문에 그대로 끼워
    넣는 사례가 실사용 중 확인됐다(2026-07-14) — 질문 하나가 통째로 아무 맥락
    없이 산문 중간에 섞여 들어가는 심각한 오염이라, 이벤트 추출 단계의 backstop과
    달리 원본 session_prose 자체에서부터 걸러낸다. 문장 단위로 쪼개 assistant
    턴 원문에 그대로(부분 문자열로) 들어있는 문장만 제거한다."""
    assistant_texts = [turn["content"] for turn in chat_turns if turn.get("role") == "assistant"]
    if not assistant_texts:
        return prose

    kept_sentences = []
    for sentence in nli.split_sentences(prose):
        leaked = len(sentence) >= _MIN_INTERVIEWER_LEAK_QUOTE_LENGTH and any(
            sentence in assistant_text for assistant_text in assistant_texts
        )
        if leaked:
            logging.getLogger(__name__).warning(
                "인터뷰어 발화가 산문 재조립에 새어 들어와 제거함: %r", sentence
            )
            continue
        kept_sentences.append(sentence)
    return " ".join(kept_sentences)


def _filter_interviewer_leakage(
    *, extracted: list[dict], chat_turns: list[dict[str, str]]
) -> tuple[list[dict], dict[int, int]]:
    """이벤트 추출이 인터뷰어(assistant)의 발화(맞장구·감사 인사·화제 전환 등)를
    화자의 사건으로 오추출하는 걸 막는 코드 레벨 backstop.

    PROSE_REASSEMBLY_SYSTEM_PROMPT가 "assistant 턴은 산문에 포함하지 말라"고
    지시하지만 LLM이 항상 지키는 건 아니다 — 실제로 "다음 이야기로 넘어가
    볼까요?" 같은 인터뷰어의 마무리 인사가 재조립 단계를 새어 나가 narrator
    사건("인터뷰어에게 감사 인사 전달")으로 잘못 추출된 사례가 있었다
    (evals/results/pilot_2026-07-12/p02_park_youngsoo.json). source_quote가
    assistant 턴 원문에 그대로 들어있으면 그 이벤트는 폐기한다 —
    _passes_distortion_check가 이미 같은 방식(role 기반 필터링)으로 원문
    premise를 사용자 발화로만 제한하는 것과 동일한 발상이다.

    반환값의 index_map은 {원본 extracted 인덱스: 살아남은 뒤의 새 인덱스}다 —
    relations의 from_index/to_index가 원본 인덱스를 참조하므로, 이벤트를
    걸러내 인덱스가 밀리면 그대로 두면 안 되고 이 맵으로 다시 정렬해야 한다.
    """
    assistant_texts = [turn["content"] for turn in chat_turns if turn.get("role") == "assistant"]
    kept: list[dict] = []
    index_map: dict[int, int] = {}
    for original_index, item in enumerate(extracted):
        quote = (item.get("source_quote") or "").strip()
        leaked = len(quote) >= _MIN_INTERVIEWER_LEAK_QUOTE_LENGTH and any(
            quote in assistant_text for assistant_text in assistant_texts
        )
        if leaked:
            logging.getLogger(__name__).warning(
                "인터뷰어 발화가 사건으로 오추출돼 폐기함: %r", quote
            )
            continue
        index_map[original_index] = len(kept)
        kept.append(item)
    return kept, index_map


# 재조립을 생성하는 모델(solar.DEFAULT_MODEL == solar-pro3)이 아니라 solar-mini를
# 판정에 쓴다 — clients/groundedness.py의 실측(2026-07-18, n=20쌍)이 "같은 계열
# 모델이 자기 출력을 검증하면 자기선호 편향으로 위험한 방향의 오판(날조를 통과시킴)이
# 늘어난다"를 이미 확인했다(solar-mini 0/10 vs solar-pro3 2/10, 위 클라이언트 모듈
# docstring 참조) — "크니까 여기 더 적합하지 않겠냐"는 유혹을 그 실측 결과를
# 근거로 거절한다.
_DISTORTION_JUDGE_MODEL = "solar-mini"


async def _passes_distortion_check(
    *, original_turns: list[dict[str, str]], reassembled_prose: str
) -> bool:
    """
    왜곡 자동 탐지. 재조립본(reassembled_prose)이 원본 발화에 없는 사실을 지어내지
    않았는지 Solar LLM 판정으로 확인한다. 재조립본은 사용자 발화만 이어 붙인
    것이므로(app/agents/prompts.py PROSE_REASSEMBLY_SYSTEM_PROMPT 참조), 원문 쪽
    비교 대상도 assistant 턴을 제외한 사용자 발화만 모아 사용한다.

    원래는 로컬 NLI(mDeBERTa) 문장 단위 entailment 배치 판정이었는데, 실사용 중
    세션 하나에 190~210초가 걸려(로컬 CPU 추론 — GPU 없는 개발 환경) process_seeded_
    sessions.py의 스테이지 타임아웃(240초)을 반복적으로 넘기는 문제가 확인됐다
    (2026-07-19). autobiography_service.py의 _run_groundedness_check가 겪은 것과
    같은 문제(그쪽은 "챕터 하나에 20분")를 같은 방식(Solar LLM 판정으로 교체,
    2026-07-17)으로 해소한 전례를 그대로 따른다 — 판정 기준(무엇을 지어낸 것으로
    볼지)은 GROUNDEDNESS_JUDGE_SYSTEM_PROMPT보다 엄격하다: 이 재조립본은 화자의
    말만 담아야 하는 축어 자료라 챕터 집필처럼 "문학적 정교화"를 봐줄 이유가 없다
    (app/agents/prompts.py DISTORTION_CHECK_SYSTEM_PROMPT 참조).

    출력 계약을 JSON 스키마가 아니라 단문 프로토콜(PASS / FAIL: 사유)로 둔 이유는
    clients/groundedness.py와 같다 — solar-mini가 Structured Outputs(response_format
    json_schema)를 solar-pro3처럼 신뢰성 있게 지원하는지 실측된 적이 없어, 이미
    검증된 단문 프로토콜만 쓴다.
    """
    original_text = "\n".join(
        turn["content"] for turn in original_turns if turn.get("role") == "user"
    )
    if not original_text.strip() or not reassembled_prose.strip():
        return True  # 비교할 원문/재조립본이 없으면 판정 자체가 불가능 — 통과 처리

    response = await solar.chat_completion(
        prompts.build_distortion_check_prompt(
            original_text=original_text, reassembled_prose=reassembled_prose
        ),
        model=_DISTORTION_JUDGE_MODEL,
        max_tokens=200,
    )
    verdict = (response.choices[0].message.content or "").strip()
    if verdict.startswith("PASS"):
        return True
    # 규약 밖 응답(빈 문자열, 형식 이탈 등)도 안전하게 "실패"로 처리한다 — 검증
    # 실패가 검증 통과로 둔갑하면 안 된다는 원칙은 clients/groundedness.py와 동일.
    logging.getLogger(__name__).warning("산문 재조립 왜곡 탐지 실패(solar-mini): %s", verdict)
    return False


async def _persist_events(
    gateways: Gateways,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    extracted: list[dict],
    is_must_include: bool = False,
) -> list[EventRecord]:
    if not extracted:
        return []

    create_data = [
        EventCreateData(
            user_id=user_id,
            source_type=EventSourceType.SESSION_CHAT,
            session_id=session_id,
            occurred_at_label=item.get("occurred_at_label"),
            place=item.get("place"),
            people=item.get("people"),
            one_line_summary=item["one_line_summary"],
            prose_paragraph=item["prose_paragraph"],
            emotion_tag=item.get("emotion_tag"),
            emotion_intensity=item.get("emotion_intensity"),
            emotion_inferred=bool(item.get("emotion_inferred", False)),
            # values_reflected(가치관, 필수 슬롯)와 reason/process(왜/어떻게, 저장 전용
            # 보강 필드) + 선택 슬롯 6개(감사·후회·전환점·자부심·신념·메시지)를 전부
            # 담는다. event_subject(narrator/other_person)는 화자 본인 사건인지
            # 화자가 전하는 제3자 사건인지 구분하는 라벨 — Phase 3 중요도 스코어링·
            # 등장인물 검토(Phase 4)가 이 값을 참조할 수 있다.
            labels={
                "values_reflected": item.get("values_reflected"),
                "reason": item.get("reason"),
                "process": item.get("process"),
                "gratitude": item.get("gratitude"),
                "regret": item.get("regret"),
                "turning_point": item.get("turning_point"),
                "pride": item.get("pride"),
                "belief": item.get("belief"),
                "message": item.get("message"),
                "event_subject": item.get("event_subject"),
                # 추정 서기 연도(자유 문자열 occurred_at_label의 정규화 보완,
                # 2026-07-18) — 챕터 배정의 시기 정합 보정과 시간 범위 강제가
                # 읽는다. DB 컬럼을 새로 파지 않고 labels(자유 JSON)에 싣는다.
                "estimated_year_start": item.get("estimated_year_start"),
                "estimated_year_end": item.get("estimated_year_end"),
            },
            confidence={
                "place": item.get("place_confidence"),
                "occurred_at_label": item.get("occurred_at_confidence"),
            },
            source_span={"quoted_text": item.get("source_quote")},
            verified=True,  # SESSION_CHAT: 왜곡 탐지 통과 시 즉시 승격 (모듈 docstring 참조)
            is_must_include=is_must_include,
        )
        for item in extracted
    ]
    events = await gateways.events.bulk_create(create_data)

    vectors = await embeddings.embed_passages([e.prose_paragraph for e in events])
    await gateways.events.bulk_update_embeddings(
        [(event.id, vector) for event, vector in zip(events, vectors)]
    )
    return events


async def _persist_relations(
    gateways: Gateways, *, events: list[EventRecord], relations: list[dict], index_map: dict[int, int]
) -> None:
    """relations의 from_index/to_index는 필터링 전 extracted 배열 기준이다
    (_filter_interviewer_leakage 참조) — index_map으로 걸러진 뒤의 인덱스로
    다시 옮기고, 둘 중 하나라도 폐기된 이벤트를 가리키면 그 관계 자체를 버린다."""
    valid_relations = []
    for relation in relations:
        from_index = index_map.get(relation["from_index"])
        to_index = index_map.get(relation["to_index"])
        if from_index is None or to_index is None:
            continue
        if not (0 <= from_index < len(events) and 0 <= to_index < len(events)):
            continue
        valid_relations.append(
            EventRelationCreateData(
                from_event_id=events[from_index].id,
                to_event_id=events[to_index].id,
                relation_type=relation["relation_type"],
            )
        )
    if valid_relations:
        await gateways.events.create_relations(valid_relations)
