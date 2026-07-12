"""
Phase 2 후처리 (세션 종료 즉시, Celery 비동기): 산문 재조립 → 왜곡 탐지 → 이벤트 분할·
라벨 추출 → 임베딩. app/workers/tasks.py의 Celery 태스크가 이 서비스를 호출한다.

SESSION_CHAT 출처 이벤트는 사용자가 인터뷰에서 직접 발화한 내용이므로, Document Parse
경로(OCR 오인식 확인 질문을 거쳐야 승격되는 DOCUMENT 출처와 달리) 재조립본이 왜곡 탐지를
통과하면 곧바로 verified=true로 저장한다. DOCUMENT 출처 이벤트의 verified 승격은 이
파일 하단의 list_pending_ocr_confirmations/resolve_ocr_confirmation이 담당하며,
app/services/interview_service.py의 인터뷰 턴에서 실제로 확인 질문을 내고 응답을
반영한다(2026-07-12 연결 — 그전까지는 verified=false로 격리된 채 승격 경로가 없었다).
"""

from __future__ import annotations

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


async def process_completed_session(gateways: Gateways, session_id: uuid.UUID) -> list[EventRecord]:
    session = await gateways.sessions.get_by_id(session_id)
    if session is None:
        raise ValueError(f"InterviewSession {session_id} not found")

    chat_turns = [{"role": log.role.value, "content": log.content} for log in session.chat_logs]
    prose_response = await solar.chat_completion(
        prompts.build_prose_reassembly_prompt(chat_turns=chat_turns),
        reasoning_effort="low",
    )
    session_prose = prose_response.choices[0].message.content or ""
    await gateways.sessions.set_session_prose(session_id, session_prose)

    if not await _passes_distortion_check(original_turns=chat_turns, reassembled_prose=session_prose):
        # 왜곡 임계값 초과: 자동 재처리 대신 최소 동작으로 세션만 저장하고 이벤트 추출은
        # 보류한다. TODO(향후 작업): 지금은 조용히 스킵만 하는데, 실제로는 재조립을
        # 재시도하거나 최종 검토 화면에 "이 세션은 검증 보류" 플래그를 노출해야 한다.
        await gateways.commit()
        return []

    extraction = await solar.structured_completion(
        prompts.build_event_extraction_prompt(session_prose=session_prose),
        schema_name="event_extraction",
        json_schema=prompts.EVENT_EXTRACTION_SCHEMA,
        reasoning_effort="medium",
    )

    events = await _persist_events(
        gateways, user_id=session.user_id, session_id=session_id, extracted=extraction.get("events", [])
    )
    await _persist_relations(gateways, events=events, relations=extraction.get("relations", []))

    await gateways.commit()
    return events


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
    gateways: Gateways, *, events: list[EventRecord], relations: list[dict]
) -> None:
    valid_relations = [
        EventRelationCreateData(
            from_event_id=events[relation["from_index"]].id,
            to_event_id=events[relation["to_index"]].id,
            relation_type=relation["relation_type"],
        )
        for relation in relations
        if 0 <= relation["from_index"] < len(events) and 0 <= relation["to_index"] < len(events)
    ]
    if valid_relations:
        await gateways.events.create_relations(valid_relations)


async def list_pending_ocr_confirmations(gateways: Gateways, user_id: uuid.UUID) -> list[EventRecord]:
    """interview_service.add_user_turn이 다음에 물어볼 확인 질문 대상을 고를 때 쓴다."""
    return await gateways.events.list_pending_document_confirmation(user_id)


async def resolve_ocr_confirmation(
    gateways: Gateways, event_id: uuid.UUID, *, confirmed: bool
) -> None:
    """OCR 확인 질문에 대한 유저 응답을 반영한다. 확인(confirmed=True)이면 다른
    DOCUMENT 이벤트와 동일하게 verified=true로 승격하고 임베딩을 계산해 RAG 검색
    대상에 포함시킨다(media_service._persist_events의 검증 경로와 동일). 부인
    (confirmed=False)이면 OCR 오인식으로 보고 폐기한다 — verified=false로 남겨두면
    다음에도 계속 같은 걸 물어보게 되므로, 격리 큐에서 완전히 빼내는 유일한 방법은
    삭제뿐이다."""
    if not confirmed:
        await gateways.events.delete(event_id)
        await gateways.commit()
        return

    event = await gateways.events.set_verified(event_id, verified=True)
    vectors = await embeddings.embed_passages([event.prose_paragraph])
    await gateways.events.bulk_update_embeddings([(event.id, vectors[0])])
    await gateways.commit()
