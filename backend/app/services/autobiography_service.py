"""
Phase 3(이벤트 병합·중요도 산정·스타일 바이블)과 Phase 4(동적 목차·하향식 집필·
팩트체크·근거검증·제3자 위해성 분류) 오케스트레이션.

Phase 3/4는 무거운 LLM 호출이 여러 번 이어지는 연산이므로(기획안 4절: "세션 종료 후
이벤트 추출, 최종 집필 ... Celery+Redis 독립 워커에서 처리"), 이 모듈의 최상위 진입점
(consolidate_autobiography, write_chapter, finalize_manuscript)은 app/workers/tasks.py의
Celery 태스크를 통해 호출되어야 API 서버 타임아웃을 피할 수 있다. 목차 생성/선택처럼
LLM 호출 1회로 끝나는 가벼운 단계는 API 요청 경로에서 직접 await해도 무방하다.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import prompts
from app.clients import embeddings as embeddings_client
from app.clients import solar
from app.models import (
    Autobiography,
    AutobiographyStatus,
    ChapterDraft,
    DraftStatus,
    Event,
    InterviewSession,
)
from app.services import character_service

# Phase 3 이벤트 병합: 이 값보다 코사인 거리가 가까운(=유사한) 쌍만 LLM 병합 판정에
# 회부한다. Upstage 임베딩 실측 전 잠정값 — 실제 임베딩으로 캘리브레이션 필요.
EVENT_MERGE_CANDIDATE_MAX_DISTANCE = 0.2
EVENT_MERGE_CANDIDATE_LIMIT = 3

# Phase 3 중요도 스코어링 가중치. 사용자 명시 지정('꼭 넣기')은 다른 신호를 압도하는
# 고정 우선순위여야 하므로(기획안: "최우선 고정") 큰 상수로 그 외 신호와 분리한다.
MUST_INCLUDE_BONUS = 1000.0
MILESTONE_BONUS = 2.0
WEIGHT_LENGTH_Z = 1.0
WEIGHT_EMOTION_INTENSITY = 0.5
WEIGHT_MENTION_COUNT = 1.5

# Phase 4 하이브리드 검색(의미 검색 + 키워드 정확 매칭)에서 챕터당 소환할 이벤트 상한.
CHAPTER_RETRIEVAL_LIMIT = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create_autobiography(db: AsyncSession, user_id: uuid.UUID) -> Autobiography:
    result = await db.execute(select(Autobiography).where(Autobiography.user_id == user_id))
    autobiography = result.scalar_one_or_none()
    if autobiography is not None:
        return autobiography

    autobiography = Autobiography(user_id=user_id)
    db.add(autobiography)
    await db.commit()
    await db.refresh(autobiography)
    return autobiography


async def get_autobiography_by_id(db: AsyncSession, autobiography_id: uuid.UUID) -> Autobiography:
    autobiography = await db.get(Autobiography, autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {autobiography_id} not found")
    return autobiography


async def list_chapter_drafts(db: AsyncSession, autobiography_id: uuid.UUID) -> list[ChapterDraft]:
    result = await db.execute(
        select(ChapterDraft)
        .where(ChapterDraft.autobiography_id == autobiography_id)
        .order_by(ChapterDraft.chapter_index.asc())
    )
    return list(result.scalars().all())


async def get_chapter_draft(db: AsyncSession, chapter_draft_id: uuid.UUID) -> ChapterDraft | None:
    return await db.get(ChapterDraft, chapter_draft_id)


# --------------------------------------------------------------------------- #
# Phase 3: 이벤트 병합 · 중요도 산정 · 스타일 바이블                            #
# --------------------------------------------------------------------------- #

async def consolidate_autobiography(db: AsyncSession, user_id: uuid.UUID) -> Autobiography:
    """
    모든 인터뷰 세션 종료 후 호출되는 Phase 3 진입점. 순서가 중요하다 — 병합을 먼저
    끝내야 중복 흡수된 이벤트가 중요도 산정의 반복 언급 신호(mention_count)에 반영된다.
    """
    autobiography = await get_or_create_autobiography(db, user_id)

    autobiography.consolidated_content = await _build_consolidated_content(db, user_id)
    await _merge_duplicate_events(db, user_id)
    await _score_importance(db, user_id)
    await _generate_style_bible(db, user_id, autobiography)

    autobiography.status = AutobiographyStatus.CONSOLIDATED
    await db.commit()
    await db.refresh(autobiography)
    return autobiography


async def _completed_session_prose(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == user_id, InterviewSession.session_prose.is_not(None))
        .order_by(InterviewSession.started_at.asc())
    )
    return [s.session_prose for s in result.scalars().all() if s.session_prose]


async def _build_consolidated_content(db: AsyncSession, user_id: uuid.UUID) -> str:
    """Autobiography.consolidated_content: 완료된 세션의 산문을 시간순으로 이어붙인
    열람용 원본. LLM 입력으로 재사용하지 않는다(모델 docstring 참조)."""
    return "\n\n".join(await _completed_session_prose(db, user_id))


async def _fetch_mergeable_events(db: AsyncSession, user_id: uuid.UUID) -> list[Event]:
    result = await db.execute(
        select(Event)
        .where(
            Event.user_id == user_id,
            Event.verified.is_(True),
            Event.duplicate_of_event_id.is_(None),
            Event.embedding.is_not(None),
        )
        .order_by(Event.created_at.asc())
    )
    return list(result.scalars().all())


async def _merge_duplicate_events(db: AsyncSession, user_id: uuid.UUID) -> None:
    """
    Phase 3 이벤트 병합·정합성 검토(기획안). 임베딩 유사도는 병합 '후보' 탐색에만
    쓰고, 실제 병합 여부는 LLM 쌍별 판정으로 결정한다. 판정이 불확실하면 병합하지
    않는 것이 기본값이다(과병합은 인쇄 후 회복 불가, 과분리는 사용자 확인으로 즉시
    회복 가능하다는 리스크 비대칭 — prompts.EVENT_MERGE_JUDGE_SYSTEM_PROMPT 참조).
    """
    canonical_candidates = await _fetch_mergeable_events(db, user_id)

    for canonical in canonical_candidates:
        if canonical.duplicate_of_event_id is not None:
            continue  # 이전 반복에서 이미 다른 이벤트로 흡수됨

        result = await db.execute(
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
                Event.embedding.is_not(None),
                Event.id != canonical.id,
                Event.embedding.cosine_distance(canonical.embedding) < EVENT_MERGE_CANDIDATE_MAX_DISTANCE,
            )
            .order_by(Event.embedding.cosine_distance(canonical.embedding))
            .limit(EVENT_MERGE_CANDIDATE_LIMIT)
        )
        for candidate in result.scalars().all():
            if candidate.duplicate_of_event_id is not None:
                continue
            if await _judge_same_event(canonical, candidate):
                candidate.duplicate_of_event_id = canonical.id

    await db.flush()


async def _judge_same_event(event_a: Event, event_b: Event) -> bool:
    messages = prompts.build_event_merge_judge_prompt(
        event_a_summary=f"{event_a.one_line_summary} ({event_a.occurred_at_label or '시기 미상'})",
        event_b_summary=f"{event_b.one_line_summary} ({event_b.occurred_at_label or '시기 미상'})",
    )
    result = await solar.structured_completion(
        messages,
        schema_name="event_merge_judge",
        json_schema=prompts.EVENT_MERGE_JUDGE_SCHEMA,
        reasoning_effort="low",
    )
    return bool(result.get("same_event", False))


async def _fetch_mention_counts(db: AsyncSession, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not event_ids:
        return {}
    result = await db.execute(
        select(Event.duplicate_of_event_id, func.count())
        .where(Event.duplicate_of_event_id.in_(event_ids))
        .group_by(Event.duplicate_of_event_id)
    )
    return dict(result.all())


async def _score_importance(db: AsyncSession, user_id: uuid.UUID) -> None:
    """
    Phase 3 객관적 중요도 스코어링. LLM 주관 판단이 아니라 계산 가능한 신호의
    가중합 + 사용자 내 z-score 정규화(발화량 편차 보정)로 산정한다(기획안).
    importance_signals에 산출 근거를 남겨 "왜 이 사건이 목차에 들어갔는가"를
    재현 가능하게 설명한다.
    """
    result = await db.execute(
        select(Event).where(
            Event.user_id == user_id,
            Event.verified.is_(True),
            Event.duplicate_of_event_id.is_(None),
        )
    )
    events = list(result.scalars().all())
    if not events:
        return

    lengths = [len(event.prose_paragraph) for event in events]
    mean_length = statistics.mean(lengths)
    stdev_length = statistics.pstdev(lengths)
    mention_counts = await _fetch_mention_counts(db, [event.id for event in events])

    for event in events:
        z_length = (len(event.prose_paragraph) - mean_length) / stdev_length if stdev_length > 0 else 0.0
        mention_count = mention_counts.get(event.id, 0) + 1  # +1: 본인 자신도 1회 언급으로 계산
        milestone = prompts.classify_life_milestone_category(
            f"{event.one_line_summary} {event.prose_paragraph}"
        )
        event.life_milestone_category = milestone

        score = (
            WEIGHT_LENGTH_Z * z_length
            + WEIGHT_EMOTION_INTENSITY * (event.emotion_intensity or 0)
            + WEIGHT_MENTION_COUNT * (mention_count - 1)
            + (MILESTONE_BONUS if milestone else 0.0)
            + (MUST_INCLUDE_BONUS if event.is_must_include else 0.0)
        )
        event.importance_score = Decimal(str(round(score, 3)))
        event.importance_signals = {
            "raw_length": len(event.prose_paragraph),
            "z_length": round(z_length, 3),
            "emotion_intensity": event.emotion_intensity,
            "mention_count": mention_count,
            "life_milestone_category": milestone,
            "is_must_include": event.is_must_include,
        }

    await db.flush()


async def _generate_style_bible(
    db: AsyncSession, user_id: uuid.UUID, autobiography: Autobiography
) -> None:
    all_prose = await _completed_session_prose(db, user_id)
    if not all_prose:
        return

    response = await solar.chat_completion(
        prompts.build_style_bible_prompt(all_session_prose=all_prose),
        reasoning_effort="medium",
    )
    autobiography.style_bible = {
        "generated_at": _now_iso(),
        "content": response.choices[0].message.content or "",
    }


# --------------------------------------------------------------------------- #
# Phase 4: 동적 목차 · 하향식 집필 · 팩트체크 · 근거검증 · 등장인물 스캔        #
# --------------------------------------------------------------------------- #

async def generate_toc_candidates(db: AsyncSession, autobiography_id: uuid.UUID) -> Autobiography:
    autobiography = await get_autobiography_by_id(db, autobiography_id)

    result = await db.execute(
        select(Event)
        .where(
            Event.user_id == autobiography.user_id,
            Event.verified.is_(True),
            Event.duplicate_of_event_id.is_(None),
        )
        .order_by(Event.importance_score.desc().nullslast())
    )
    events = list(result.scalars().all())
    if not events:
        raise ValueError("목차를 생성하려면 먼저 Phase 3(consolidate_autobiography)이 완료되어야 합니다.")

    summaries_block = "\n".join(
        f"- [중요도 {event.importance_score}] {event.one_line_summary} "
        f"(시기: {event.occurred_at_label or '미상'}, 감정: {event.emotion_tag or '미상'})"
        for event in events
    )
    result_json = await solar.structured_completion(
        prompts.build_toc_generation_prompt(event_summaries_with_scores=summaries_block),
        schema_name="toc_generation",
        json_schema=prompts.TOC_GENERATION_SCHEMA,
        reasoning_effort="medium",
    )
    autobiography.toc_data = {
        "generated_at": _now_iso(),
        "candidates": result_json["candidates"],
        "selected_candidate_index": None,
    }
    await db.commit()
    await db.refresh(autobiography)
    return autobiography


async def select_toc_candidate(
    db: AsyncSession, autobiography_id: uuid.UUID, candidate_index: int
) -> Autobiography:
    autobiography = await get_autobiography_by_id(db, autobiography_id)
    if not autobiography.toc_data or not autobiography.toc_data.get("candidates"):
        raise ValueError("먼저 목차 후보를 생성해야 합니다(generate_toc_candidates).")

    candidates = autobiography.toc_data["candidates"]
    if not (0 <= candidate_index < len(candidates)):
        raise ValueError(f"candidate_index={candidate_index}가 후보 범위를 벗어났습니다(총 {len(candidates)}개).")

    chosen = candidates[candidate_index]
    autobiography.toc_data = {**autobiography.toc_data, "selected_candidate_index": candidate_index}

    # 재선택 시 이전 챕터 초안을 대체한다(idempotent).
    await db.execute(delete(ChapterDraft).where(ChapterDraft.autobiography_id == autobiography.id))
    for chapter in chosen["chapters"]:
        db.add(
            ChapterDraft(
                autobiography_id=autobiography.id,
                chapter_index=chapter["chapter_index"],
                title=chapter["title"],
            )
        )

    autobiography.book_synopsis = await _generate_book_synopsis(autobiography, chosen)
    await db.commit()
    await db.refresh(autobiography)
    return autobiography


async def _generate_book_synopsis(autobiography: Autobiography, selected_toc: dict) -> str:
    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    toc_text = "\n".join(
        f"{chapter['chapter_index']}. {chapter['title']} ({', '.join(chapter.get('theme_keywords', []))})"
        for chapter in selected_toc["chapters"]
    )
    response = await solar.chat_completion(
        prompts.build_book_synopsis_prompt(style_bible=style_bible_text, toc=toc_text),
        reasoning_effort="medium",
    )
    return response.choices[0].message.content or ""


async def _retrieve_events_for_chapter(
    db: AsyncSession, user_id: uuid.UUID, chapter: ChapterDraft
) -> list[Event]:
    """
    하이브리드 검색(의미 검색 + 키워드 정확 매칭). ChapterDraft는 theme_keywords를
    영속화하지 않으므로(이번 작업은 서비스 레이어로 범위를 한정했다 — DB 스키마
    확장은 별도 논의 대상) 챕터 제목을 쿼리로 사용하는 1차 근사치다.
    """
    query_text = chapter.title or ""
    semantic_ids: list[uuid.UUID] = []
    if query_text:
        query_vector = await embeddings_client.embed_query(query_text)
        result = await db.execute(
            select(Event.id)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
            )
            .order_by(Event.embedding.cosine_distance(query_vector))
            .limit(CHAPTER_RETRIEVAL_LIMIT)
        )
        semantic_ids = list(result.scalars().all())

    keyword_ids: list[uuid.UUID] = []
    keywords = [word for word in query_text.split() if len(word) >= 2]
    if keywords:
        keyword_filter = or_(*[Event.one_line_summary.ilike(f"%{kw}%") for kw in keywords])
        result = await db.execute(
            select(Event.id)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
                keyword_filter,
            )
            .limit(CHAPTER_RETRIEVAL_LIMIT)
        )
        keyword_ids = list(result.scalars().all())

    merged_ids = list(dict.fromkeys([*semantic_ids, *keyword_ids]))[:CHAPTER_RETRIEVAL_LIMIT]
    if not merged_ids:
        return []

    result = await db.execute(
        select(Event).where(Event.id.in_(merged_ids)).order_by(Event.importance_score.desc().nullslast())
    )
    return list(result.scalars().all())


async def _previous_chapter_summary(
    db: AsyncSession, autobiography_id: uuid.UUID, chapter_index: int
) -> str | None:
    if chapter_index <= 1:
        return None
    result = await db.execute(
        select(ChapterDraft).where(
            ChapterDraft.autobiography_id == autobiography_id,
            ChapterDraft.chapter_index == chapter_index - 1,
        )
    )
    previous = result.scalar_one_or_none()
    if previous is None or not previous.content:
        return None
    # 직전 챕터 전문 대신 말미 일부만 전달 — 실제 요약 생성 LLM 호출을 아끼는 근사치.
    return previous.content[-1000:]


async def write_chapter(db: AsyncSession, chapter_draft_id: uuid.UUID) -> ChapterDraft:
    """
    Phase 4 하향식 집필의 챕터 단위 실행: [챕터 시놉시스 생성 → 하이브리드 RAG 소환 →
    본문 집필 → 팩트체크 → 근거검증 → 등장인물 스캔]을 한 챕터에 대해 순서대로 수행한다.
    """
    chapter = await db.get(
        ChapterDraft, chapter_draft_id, options=[selectinload(ChapterDraft.autobiography)]
    )
    if chapter is None:
        raise ValueError(f"ChapterDraft {chapter_draft_id} not found")
    autobiography = chapter.autobiography
    if not autobiography.book_synopsis:
        raise ValueError("먼저 목차를 선택해 책 전체 시놉시스를 생성해야 합니다(select_toc_candidate).")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")

    retrieved_events = await _retrieve_events_for_chapter(db, autobiography.user_id, chapter)
    chapter.source_event_ids = [event.id for event in retrieved_events]

    chapter.chapter_synopsis = await _generate_chapter_synopsis(
        book_synopsis=autobiography.book_synopsis,
        chapter_title=chapter.title or f"{chapter.chapter_index}장",
        event_summaries=[event.one_line_summary for event in retrieved_events],
    )

    previous_summary = await _previous_chapter_summary(db, autobiography.id, chapter.chapter_index)

    chapter.content = await _generate_chapter_content(
        style_bible=style_bible_text,
        book_synopsis=autobiography.book_synopsis,
        chapter_synopsis=chapter.chapter_synopsis,
        previous_chapter_summary=previous_summary,
        retrieved_event_paragraphs=[event.prose_paragraph for event in retrieved_events],
    )

    chapter.factcheck_report = await _run_factcheck(chapter.content, source_events=retrieved_events)
    chapter.groundedness_report = _run_groundedness_check_placeholder(
        chapter.content, source_events=retrieved_events
    )
    await character_service.scan_and_classify_chapter(db, chapter=chapter, autobiography=autobiography)

    chapter.status = DraftStatus.REVIEWED
    await db.commit()
    await db.refresh(chapter)
    return chapter


async def _generate_chapter_synopsis(
    *, book_synopsis: str, chapter_title: str, event_summaries: list[str]
) -> str:
    response = await solar.chat_completion(
        prompts.build_chapter_synopsis_prompt(
            book_synopsis=book_synopsis, chapter_title=chapter_title, event_summaries=event_summaries,
        ),
        reasoning_effort="medium",
    )
    return response.choices[0].message.content or ""


async def _generate_chapter_content(
    *,
    style_bible: str,
    book_synopsis: str,
    chapter_synopsis: str,
    previous_chapter_summary: str | None,
    retrieved_event_paragraphs: list[str],
) -> str:
    response = await solar.chat_completion(
        prompts.build_chapter_writing_prompt(
            style_bible=style_bible,
            book_synopsis=book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_chapter_summary,
            retrieved_event_paragraphs=retrieved_event_paragraphs,
        ),
        reasoning_effort="high",
    )
    return response.choices[0].message.content or ""


async def _run_factcheck(chapter_content: str, *, source_events: list[Event]) -> dict:
    """
    원문 대조 팩트체크(재추출-정규화-대조). 개체 정규화(연도 절대환산, 지명 정규
    명칭, 인명 별칭 매핑)는 본격적인 엔티티 리졸루션이 필요한 작업이라, 이 구현은
    대소문자 무시 부분 문자열 매칭으로 단순화했다 — 기획안이 요구하는 전체 정규화
    파이프라인의 1차 근사치이며, 정밀도를 높이려면 실제 정규화 테이블/개체 연결
    로직 도입이 필요하다.
    """
    if not chapter_content.strip():
        return {"checked_at": _now_iso(), "total_facts": 0, "unchecked_facts": 0, "flags": []}

    extraction = await solar.structured_completion(
        prompts.build_fact_reextraction_prompt(chapter_content=chapter_content),
        schema_name="fact_reextraction",
        json_schema=prompts.FACT_REEXTRACTION_SCHEMA,
        reasoning_effort="low",
    )
    facts = extraction.get("facts", [])

    expected_places = {e.place.lower() for e in source_events if e.place}
    expected_people = {e.people.lower() for e in source_events if e.people}
    expected_time_labels = {e.occurred_at_label.lower() for e in source_events if e.occurred_at_label}
    expected_pools = {
        "place": expected_places,
        "person": expected_people,
        "year_or_age": expected_time_labels,
    }

    flags = []
    unchecked = 0
    for fact in facts:
        fact_type = fact["fact_type"]
        if fact_type == "quantity":
            # Event 모델에 수량 필드가 없어 대조 기준이 없다 — 오탐 방지를 위해 검증
            # 대상에서 제외하고 unchecked로만 집계한다.
            unchecked += 1
            continue
        raw_text = fact["raw_text"].strip().lower()
        expected_pool = expected_pools.get(fact_type, set())
        matched = any(raw_text in expected or expected in raw_text for expected in expected_pool)
        if not matched:
            flags.append(
                {"fact_type": fact_type, "raw_text": fact["raw_text"], "reason": "no_matching_source_label"}
            )

    return {
        "checked_at": _now_iso(),
        "total_facts": len(facts),
        "unchecked_facts": unchecked,
        "flags": flags,
    }


def _run_groundedness_check_placeholder(chapter_content: str, *, source_events: list[Event]) -> dict:
    """
    TODO(미구현): 공개 한국어 NLI 모델로 문장-출처 이벤트 문단 쌍의 함의(entailment)를
    판정해야 한다(기획안: "생성된 각 문장을 소환된 이벤트 문단과 쌍으로 구성해 NLI로
    판정, 함의되지 않는 진술은 플래그"). 로컬 모델 서빙 방식이 정해지기 전까지는
    event_extraction_service._passes_distortion_check와 동일하게 항상 통과시키는
    자리표시자이며, checked=False는 이 검증이 실제로 수행되지 않았다는 신호로 최종
    검토 화면에 노출되어야 한다.
    """
    return {
        "checked": False,
        "flags": [],
        "note": "NLI 로컬 모델 미연동 — 자리표시자, 항상 통과 처리",
        "source_event_count": len(source_events),
        "chapter_content_length": len(chapter_content),
    }


async def finalize_manuscript(db: AsyncSession, autobiography_id: uuid.UUID) -> Autobiography:
    """
    Phase 4 통일성 윤문 패스: 전 챕터 생성 후 인접 챕터 경계부와 스타일 바이블을
    함께 검토하는 리비전을 1회 수행한다. 사실 관계·순서는 변경하지 않는다.
    """
    autobiography = await get_autobiography_by_id(db, autobiography_id)
    result = await db.execute(
        select(ChapterDraft)
        .where(ChapterDraft.autobiography_id == autobiography.id)
        .order_by(ChapterDraft.chapter_index.asc())
    )
    chapters = list(result.scalars().all())
    if not chapters or any(chapter.content is None for chapter in chapters):
        raise ValueError("모든 챕터의 집필(write_chapter)이 끝난 뒤에 최종 윤문을 수행할 수 있습니다.")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    full_manuscript = "\n\n".join(
        f"[{chapter.chapter_index}장. {chapter.title}]\n{chapter.content}" for chapter in chapters
    )
    response = await solar.chat_completion(
        prompts.build_unity_revision_prompt(style_bible=style_bible_text, full_manuscript=full_manuscript),
        reasoning_effort="high",
    )
    autobiography.final_content = response.choices[0].message.content or full_manuscript

    for chapter in chapters:
        chapter.status = DraftStatus.FINALIZED

    await db.commit()
    await db.refresh(autobiography)
    return autobiography
