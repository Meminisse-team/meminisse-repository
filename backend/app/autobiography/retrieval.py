"""Retrieval domain operations."""
from __future__ import annotations






import statistics

import uuid



from app.agents import prompts

from app.clients import embeddings as embeddings_client


from app.clients import llm_router


from app.gateways.dto import AutobiographyRecord, EventRecord

from app.gateways.factory import Gateways




async def _retrieve_events_for_chapter(
    gateways: Gateways,
    user_id: uuid.UUID,
    chapter_title: str,
    *,
    query_vector: list[float] | None = None,
) -> list[EventRecord]:
    """
    하이브리드 검색(의미 검색 + 키워드 정확 매칭). ChapterDraft는 theme_keywords를
    영속화하지 않으므로(이번 작업은 서비스 레이어로 범위를 한정했다 — DB 스키마
    확장은 별도 논의 대상) 챕터 제목을 쿼리로 사용하는 1차 근사치다. 제목 문자열만
    받는 이유: select_toc_candidate가 ChapterDraft 생성 전(목차 후보 dict 단계)에도
    같은 검색으로 챕터 시놉시스 재료를 소환해야 하기 때문.

    query_vector: 호출부가 제목 임베딩을 미리(예: 여러 챕터 분량을 병렬로) 구해뒀으면
    주입한다 — 없으면 여기서 단건 임베딩한다(select_toc_candidate가 19개 챕터를
    순차 임베딩하며 임계 경로가 늘어나던 문제의 병렬화 지점, 2026-07-18).

    두 축 모두 EventGateway.search_verified/search_by_keywords를 통하므로 Layer 1
    검증 게이트(verified=True, duplicate_of_event_id IS NULL)가 항상 적용된다.
    """
    from app.autobiography.constants import CHAPTER_RETRIEVAL_LIMIT
    query_text = chapter_title
    semantic_events: list[EventRecord] = []
    if query_text:
        if query_vector is None:
            query_vector = await embeddings_client.embed_query(query_text)
        semantic_events = await gateways.events.search_verified(
            user_id=user_id, query_embedding=query_vector, limit=CHAPTER_RETRIEVAL_LIMIT
        )

    keywords = [word for word in query_text.split() if len(word) >= 2]
    keyword_events = (
        await gateways.events.search_by_keywords(
            user_id=user_id, keywords=keywords, limit=CHAPTER_RETRIEVAL_LIMIT
        )
        if keywords
        else []
    )

    merged_ids = list(dict.fromkeys([e.id for e in semantic_events] + [e.id for e in keyword_events]))
    merged_ids = merged_ids[:CHAPTER_RETRIEVAL_LIMIT]
    if not merged_ids:
        return []
    return await gateways.events.list_by_ids(merged_ids)


def _event_estimated_year(event: EventRecord) -> int | None:
    """이벤트의 대표 연도. 1순위는 추출 단계가 정규화한 labels.estimated_year_start
    (2026-07-18 스키마 확장 — 신규 추출분부터 존재), 폴백은 occurred_at_label
    문자열 속 4자리 연도다(기존 데이터 커버)."""
    from app.autobiography.constants import _YEAR_IN_LABEL_PATTERN
    year = (event.labels or {}).get("estimated_year_start")
    if isinstance(year, int):
        return year
    if event.occurred_at_label:
        match = _YEAR_IN_LABEL_PATTERN.search(event.occurred_at_label)
        if match:
            return int(match.group(1))
    return None


def _sort_events_chronologically(events: list[EventRecord]) -> list[EventRecord]:
    """챕터 집필 프롬프트에 넘기기 직전, 사건을 시간순으로 정렬한다
    (write_chapter 참조 — 원래 순서는 중요도·검색 순위라 시간과 무관하다).
    연도를 알 수 없는 사건(_event_estimated_year가 None)은 완전히 배제하기보다
    안전하게 맨 뒤로 보낸다."""
    return sorted(
        events, key=lambda e: (_event_estimated_year(e) is None, _event_estimated_year(e) or 0)
    )


def _rebalance_assignment_by_year(
    assigned: list[list[EventRecord]],
    retrieved: list[list[EventRecord]],
) -> list[list[EventRecord]]:
    """배타적 배정(_assign_events_to_chapters)의 시기 정합 보정 1패스.

    순위 기반 배정은 중복은 막지만 "엉뚱한 챕터가 가져가는" 오배정은 못 막는다
    (2026-07-18 실측: 루카스 석좌 임명식(1979)이 검색 순위 때문에 아들 출생
    챕터(1967 중심)에 배정돼 그 챕터가 임명식 장면으로 시작함). 배정 스냅샷
    기준으로 챕터별 중앙값 연도를 구하고, 자기 챕터 중앙값과 허용 이격
    (_ASSIGNMENT_YEAR_TOLERANCE) 초과로 벌어진 이벤트를 — 원 검색 결과에 함께
    올랐던 챕터 중 중앙값이 가장 가까운 곳으로 옮긴다. 중앙값·이동 판단 모두
    보정 전 스냅샷 기준이라 입력이 같으면 결과도 항상 같다(결정론). 옮긴 뒤
    빈 챕터가 되는 이동은 하지 않는다."""
    from app.autobiography.constants import _ASSIGNMENT_YEAR_TOLERANCE
    from app.autobiography.constants import logger
    year_lists = [[_event_estimated_year(e) for e in events] for events in assigned]
    medians: list[float | None] = []
    for years in year_lists:
        known = [y for y in years if y is not None]
        medians.append(statistics.median(known) if known else None)

    appears_in: dict[uuid.UUID, list[int]] = {}
    for pos, events in enumerate(retrieved):
        for event in events:
            appears_in.setdefault(event.id, []).append(pos)

    rebalanced = [list(events) for events in assigned]
    for pos, events in enumerate(assigned):
        median = medians[pos]
        if median is None:
            continue
        for event, year in zip(events, year_lists[pos]):
            if year is None:
                continue
            gap = abs(year - median)
            if gap <= _ASSIGNMENT_YEAR_TOLERANCE:
                continue
            best_pos, best_gap = pos, gap
            for alt in appears_in.get(event.id, []):
                alt_median = medians[alt]
                if alt == pos or alt_median is None:
                    continue
                alt_gap = abs(year - alt_median)
                if alt_gap < best_gap:
                    best_pos, best_gap = alt, alt_gap
            if best_pos != pos and len(rebalanced[pos]) > 1:
                rebalanced[pos].remove(event)
                rebalanced[best_pos].append(event)
                logger.info(
                    "배정 시기 보정: 사건(연도 %d)을 챕터 순번 %d(중앙값 %.0f) → %d(중앙값 %.0f)로 이동",
                    year, pos, median, best_pos, medians[best_pos],
                )
    return rebalanced


def _assign_events_to_chapters(
    chapter_events: list[list[EventRecord]],
) -> list[list[EventRecord]]:
    """챕터별 검색 결과(챕터 간 중복 포함)를 받아 각 이벤트를 정확히 한 챕터에만
    배정한다. 같은 사건(루카스 석좌 임명 등)이 여러 챕터에서 각각 소환돼 반복
    서술되는 문제(2026-07-17 호킹 계정 실측: 같은 결혼식 문장이 3장과 9장에 거의
    동일하게 등장)를 검색 레이어에서 결정론적으로 차단한다.

    배정 규칙: 이벤트가 여러 챕터의 검색 결과에 등장하면 검색 순위(리스트 내
    인덱스, 낮을수록 관련도 높음)가 가장 좋은 챕터가 가져간다. 동순위면 앞
    챕터 우선 — 입력이 같으면 결과도 항상 같다(전 챕터 병렬 집필과 무관하게
    결정적)."""
    from app.autobiography.constants import _MIN_EVENTS_PER_CHAPTER_FALLBACK
    from app.autobiography.constants import logger
    best: dict[uuid.UUID, tuple[int, int]] = {}
    for chapter_pos, events in enumerate(chapter_events):
        for rank, event in enumerate(events):
            claim = (rank, chapter_pos)
            if event.id not in best or claim < best[event.id]:
                best[event.id] = claim

    assigned: list[list[EventRecord]] = []
    for chapter_pos, events in enumerate(chapter_events):
        kept = [
            event for rank, event in enumerate(events) if best[event.id] == (rank, chapter_pos)
        ]
        if not kept and events:
            kept = events[:_MIN_EVENTS_PER_CHAPTER_FALLBACK]
            logger.info(
                "챕터(순번 %d) 배타적 배정 결과가 비어 검색 상위 %d개로 폴백(중복 허용)",
                chapter_pos,
                len(kept),
            )
        assigned.append(kept)
    return assigned


def _chapter_time_scope(events: list[EventRecord]) -> str | None:
    """배정된 사건들의 시기 라벨(occurred_at_label)을 모아 챕터의 시간 범위 문자열로
    만든다. 집필·시놉시스 프롬프트에 "이 범위 밖 시기는 새 장면으로 서술 금지"
    지시와 함께 주입된다 — 한 챕터가 생애 후반부 전체를 흡수하는 스코프 폭주
    (호킹 계정 7장: 1963년 진단 챕터가 임종 직전까지 서술) 방지용."""
    labels = list(
        dict.fromkeys(
            event.occurred_at_label.strip()
            for event in events
            if event.occurred_at_label and event.occurred_at_label.strip()
        )
    )
    if not labels:
        return None
    return ", ".join(labels)


def _other_chapter_titles(autobiography: AutobiographyRecord, chapter_index: int) -> list[str]:
    """선택된 목차에서 이 챕터를 제외한 나머지 챕터 제목 목록. 집필 프롬프트에
    "다른 챕터에서 다룰 주제 — 이 챕터에서 본격 서술 금지"로 주입된다.
    _chapter_connecting_thread와 동일한 toc_data JSON 읽기 패턴."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return []
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return []
    return [
        f"{chapter['chapter_index']}장. {chapter.get('title') or ''}"
        for chapter in candidates[selected_index].get("chapters", [])
        if chapter.get("chapter_index") != chapter_index
    ]


async def _previous_chapter_summary(
    gateways: Gateways, autobiography_id: uuid.UUID, chapter_index: int
) -> str | None:
    """다음 챕터 집필에 넘길 직전 챕터 컨텍스트.

    1순위는 직전 챕터의 시놉시스(select_toc_candidate가 목차 확정 시점에 미리
    생성·저장) — 집필 계획이라 사건·감정·여운 신호가 이미 담겨 있고, 직전 챕터의
    '완성 본문'에 의존하지 않으므로 전 챕터를 동시에 집필해도(프론트가 이미
    Promise.all로 전 챕터를 한꺼번에 큐잉한다) 큐 순서와 무관하게 결정적으로
    같은 결과가 나온다. 예전 방식(완성 본문을 LLM으로 요약)은 챕터들을 직렬로
    묶는 병목이었을 뿐 아니라, 병렬 큐잉 시 직전 본문이 아직 없으면 조용히
    "(첫 챕터)"로 처리되는 비결정성이 있었다(2026-07-17).

    시놉시스가 없는 구버전 초안은 기존 방식(본문 요약, reasoning_effort="low")으로
    폴백한다 — 예전에는 previous.content[-1000:] 단순 절단을 썼다가 문장 중간에서
    잘린 조각이라 요약 호출로 바꾼 이력이 있다."""
    if chapter_index <= 1:
        return None
    previous = await gateways.chapters.get_by_index(autobiography_id, chapter_index - 1)
    if previous is None:
        return None
    if previous.chapter_synopsis:
        return previous.chapter_synopsis
    if not previous.content:
        return None
    response = await llm_router.chat_completion(
        prompts.build_chapter_recap_prompt(chapter_content=previous.content),
        reasoning_effort="low",
    )
    return response.choices[0].message.content or previous.content[-1000:]
