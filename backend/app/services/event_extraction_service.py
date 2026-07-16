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
import uuid

from app.agents import prompts
from app.clients import embeddings, nli, solar
from app.gateways.dto import EventCreateData, EventRecord, EventRelationCreateData
from app.gateways.factory import Gateways
from app.models.enums import EventSourceType

# 이 값을 넘는 모순(contradiction) 확률이 하나의 문장에서라도 나오면 왜곡으로 판정한다.
# 임계값을 낮게 잡을수록(민감) 재조립 품질 이슈를 더 많이 잡아내지만 오탐도 늘어난다 —
# 실사용 데이터로 캘리브레이션 전까지의 잠정값.
_DISTORTION_CONTRADICTION_THRESHOLD = 0.5

# 이보다 짧은 source_quote는 assistant 발화와 우연히 부분 문자열이 겹칠 위험이 커서
# (예: "네", "그렇군요") 인터뷰어 발화 유출 판정에서 제외한다.
_MIN_INTERVIEWER_LEAK_QUOTE_LENGTH = 4


async def process_completed_session(gateways: Gateways, session_id: uuid.UUID) -> list[EventRecord]:
    session = await gateways.sessions.get_by_id(session_id)
    if session is None:
        raise ValueError(f"InterviewSession {session_id} not found")

    chat_turns = [{"role": log.role.value, "content": log.content} for log in session.chat_logs]
    reassembly_turns = _exclude_wrap_up_exchange(chat_turns)
    prose_response = await solar.chat_completion(
        prompts.build_prose_reassembly_prompt(chat_turns=reassembly_turns),
        # "low"에서는 세션 안에 사건이 2개 이상이고 각각 후속 질문 병합이 필요할 때
        # 원본 문장과 병합 문장을 중복으로 남기는 오류가 실사용 검증(4회 중 약 2~3회)
        # 재현됐다. "medium"으로 올리면 같은 검증에서 대부분 정상 병합되고(4회 중
        # 3회 완전 정상), "high"는 오히려 21세·집에서 같은 구체적 사실을 뭉뚱그려
        # 누락시키는 별도 실패 유형이 나타나 채택하지 않았다.
        reasoning_effort="medium",
    )
    session_prose = _strip_leaked_assistant_sentences(
        prose=prose_response.choices[0].message.content or "", chat_turns=chat_turns
    )
    await gateways.sessions.set_session_prose(session_id, session_prose)

    if not await _passes_distortion_check(original_turns=chat_turns, reassembled_prose=session_prose):
        # 왜곡 임계값 초과: 자동 재처리 대신 최소 동작으로 세션만 저장하고 이벤트 추출은
        # 보류한다. TODO(향후 작업): 지금은 조용히 스킵만 하는데, 실제로는 재조립을
        # 재시도하거나 최종 검토 화면에 "이 세션은 검증 보류" 플래그를 노출해야 한다.
        await gateways.commit()
        return []

    # 세션의 첫 chat_log는 이 세션이 다룬 질문/사진 오프닝 문구다(role=assistant,
    # interview_service.py:_resolve_opening_content가 생성 시점에 저장). session_prose
    # 자체는 화자의 말만 최소 변형으로 담아야 하므로(왜곡 탐지 대상이라 손대면 안 됨,
    # _strip_leaked_assistant_sentences 참조) 이 맥락은 산문에 섞지 않고 이벤트 추출
    # 단계에만 별도로 건네 준다 — "서울대"처럼 답변이 짧아도 무엇에 대한 이야기인지
    # one_line_summary/prose_paragraph가 명확하게 나오도록(2026-07-15 피드백).
    question_context = _question_context(chat_turns)
    events = await _extract_events_from_prose(
        gateways,
        session=session,
        session_prose=session_prose,
        question_context=question_context,
        chat_turns=chat_turns,
    )

    await gateways.commit()
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


async def _extract_events_from_prose(
    gateways: Gateways,
    *,
    session,
    session_prose: str,
    question_context: str | None,
    chat_turns: list[dict[str, str]],
) -> list[EventRecord]:
    extraction = await solar.structured_completion(
        prompts.build_event_extraction_prompt(
            session_prose=session_prose, question_context=question_context
        ),
        schema_name="event_extraction",
        json_schema=prompts.EVENT_EXTRACTION_SCHEMA,
        reasoning_effort="medium",
    )

    extracted, index_map = _filter_interviewer_leakage(
        extracted=extraction.get("events", []), chat_turns=chat_turns
    )
    events = await _persist_events(
        gateways, user_id=session.user_id, session_id=session.id, extracted=extracted
    )
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


async def _passes_distortion_check(
    *, original_turns: list[dict[str, str]], reassembled_prose: str
) -> bool:
    """
    왜곡 자동 탐지(NLI 함의 검증, 기획안: "재조립본과 원문 간... 모순확률이 임계값
    초과 시 자동 재처리/플래그"). 재조립본(reassembled_prose)은 사용자 발화만 이어
    붙인 것이므로(app/agents/prompts.py PROSE_REASSEMBLY_SYSTEM_PROMPT 참조),
    원문 쪽 비교 대상도 assistant 턴을 제외한 사용자 발화만 모아 premise로 삼는다.

    재조립본을 문장 단위로 쪼개 각 문장이 원문과 모순(contradiction)되는지 개별
    판정한다 — 문장 하나라도 임계값을 넘는 모순이면 전체를 왜곡으로 판정한다(하나의
    문장이 지어낸 내용이어도 그 세션 전체의 신뢰도를 의심해야 하므로).
    """
    original_text = "\n".join(
        turn["content"] for turn in original_turns if turn.get("role") == "user"
    )
    if not original_text.strip() or not reassembled_prose.strip():
        return True  # 비교할 원문/재조립본이 없으면 판정 자체가 불가능 — 통과 처리

    sentences = nli.split_sentences(reassembled_prose)
    # 문장마다 개별 호출하면 모델 forward pass 오버헤드가 문장 수만큼 곱해져
    # 느리다(nli.classify_entailment_batch 문서 참조) — 같은 premise(원문)에 여러
    # hypothesis(문장)를 한 배치로 묶어 한 번에 판정한다.
    results = await nli.classify_entailment_batch(premise=original_text, hypotheses=sentences)
    return all(result["contradiction"] <= _DISTORTION_CONTRADICTION_THRESHOLD for result in results)


async def _persist_events(
    gateways: Gateways, *, user_id: uuid.UUID, session_id: uuid.UUID, extracted: list[dict]
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
            },
            confidence={
                "place": item.get("place_confidence"),
                "occurred_at_label": item.get("occurred_at_confidence"),
            },
            source_span={"quoted_text": item.get("source_quote")},
            verified=True,  # SESSION_CHAT: 왜곡 탐지 통과 시 즉시 승격 (모듈 docstring 참조)
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
