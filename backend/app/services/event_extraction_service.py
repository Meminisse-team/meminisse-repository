"""
Phase 2 후처리 (세션 종료 즉시, Celery 비동기): 산문 재조립 → 왜곡 탐지 → 이벤트 분할·
라벨 추출 → 임베딩. app/workers/tasks.py의 Celery 태스크가 이 서비스를 호출한다.

SESSION_CHAT 출처 이벤트는 사용자가 인터뷰에서 직접 발화한 내용이므로, Document Parse
경로(OCR 오인식 확인 질문을 거쳐야 승격되는 DOCUMENT 출처와 달리) 재조립본이 왜곡 탐지를
통과하면 곧바로 verified=true로 저장한다. DOCUMENT 출처 이벤트의 verified 승격은
media_service/추후 확인-질문 인터뷰 턴에서 처리한다(이 서비스의 책임이 아님).
"""

from __future__ import annotations

import uuid

from app.agents import prompts
from app.clients import embeddings, solar
from app.gateways.dto import EventCreateData, EventRecord, EventRelationCreateData
from app.gateways.factory import Gateways
from app.models.enums import EventSourceType


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

    if not _passes_distortion_check(original_turns=chat_turns, reassembled_prose=session_prose):
        # 왜곡 임계값 초과: 자동 재처리 대신 최소 동작으로 세션만 저장하고 이벤트 추출은
        # 보류한다(진짜 NLI 모델이 붙기 전까지는 항상 통과하므로 이 분기는 도달하지 않는다).
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


def _passes_distortion_check(*, original_turns: list[dict[str, str]], reassembled_prose: str) -> bool:
    """
    TODO(미구현): 공개 한국어 NLI 모델 로컬 추론으로 재조립본-원문 함의 검증을 수행해야 한다
    (기획안: "왜곡 자동 탐지(NLI 함의 검증)... 모순확률이 임계값 초과 시 자동 재처리/플래그").
    로컬 모델 서빙 방식(별도 프로세스/온디바이스 추론)은 ML 담당과 협의가 필요해 지금은
    항상 통과시키는 자리표시자로 둔다. 이 게이트가 실제로 동작하기 전까지 verified=true
    승격은 재조립 품질에 대한 보증이 아니라는 점에 유의할 것.
    """
    return True


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
