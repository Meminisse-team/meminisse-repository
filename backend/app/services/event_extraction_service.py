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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import prompts
from app.clients import embeddings, solar
from app.models import Event, EventRelation, EventSourceType, InterviewSession


async def process_completed_session(db: AsyncSession, session_id: uuid.UUID) -> list[Event]:
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .options(selectinload(InterviewSession.chat_logs))
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError(f"InterviewSession {session_id} not found")

    chat_turns = [
        {"role": log.role.value, "content": log.content} for log in session.chat_logs
    ]
    prose_response = await solar.chat_completion(
        prompts.build_prose_reassembly_prompt(chat_turns=chat_turns),
        reasoning_effort="low",
    )
    session_prose = prose_response.choices[0].message.content or ""
    session.session_prose = session_prose

    if not _passes_distortion_check(original_turns=chat_turns, reassembled_prose=session_prose):
        # 왜곡 임계값 초과: 자동 재처리 대신 최소 동작으로 세션만 저장하고 이벤트 추출은
        # 보류한다(진짜 NLI 모델이 붙기 전까지는 항상 통과하므로 이 분기는 도달하지 않는다).
        await db.commit()
        return []

    extraction = await solar.structured_completion(
        prompts.build_event_extraction_prompt(session_prose=session_prose),
        schema_name="event_extraction",
        json_schema=prompts.EVENT_EXTRACTION_SCHEMA,
        reasoning_effort="medium",
    )

    events = await _persist_events(
        db, session=session, extracted=extraction.get("events", []),
    )
    await _persist_relations(db, events=events, relations=extraction.get("relations", []))

    await db.commit()
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
    db: AsyncSession, *, session: InterviewSession, extracted: list[dict]
) -> list[Event]:
    events: list[Event] = []
    for item in extracted:
        event = Event(
            user_id=session.user_id,
            source_type=EventSourceType.SESSION_CHAT,
            session_id=session.id,
            life_period=None,  # 생애주기 큐 정렬은 추후 age/occurred_at_label 매핑 서비스에서 채움
            occurred_at_label=item.get("occurred_at_label"),
            place=item.get("place"),
            people=item.get("people"),
            one_line_summary=item["one_line_summary"],
            prose_paragraph=item["prose_paragraph"],
            emotion_tag=item.get("emotion_tag"),
            emotion_intensity=item.get("emotion_intensity"),
            emotion_inferred=bool(item.get("emotion_inferred", False)),
            labels={"values_reflected": item.get("values_reflected")},
            confidence={
                "place": item.get("place_confidence"),
                "occurred_at_label": item.get("occurred_at_confidence"),
            },
            source_span={"quoted_text": item.get("source_quote")},
            verified=True,  # SESSION_CHAT: 왜곡 탐지 통과 시 즉시 승격 (위 docstring 참조)
        )
        db.add(event)
        events.append(event)
    await db.flush()  # id 확보 (임베딩/관계 저장 전 필요)

    if events:
        vectors = await embeddings.embed_passages([e.prose_paragraph for e in events])
        for event, vector in zip(events, vectors):
            event.embedding = vector

    return events


async def _persist_relations(
    db: AsyncSession, *, events: list[Event], relations: list[dict]
) -> None:
    for relation in relations:
        from_index, to_index = relation["from_index"], relation["to_index"]
        if not (0 <= from_index < len(events) and 0 <= to_index < len(events)):
            continue
        db.add(
            EventRelation(
                from_event_id=events[from_index].id,
                to_event_id=events[to_index].id,
                relation_type=relation["relation_type"],
            )
        )
