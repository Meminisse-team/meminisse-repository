"""Toc domain operations."""
from __future__ import annotations


import asyncio





import uuid


from decimal import Decimal

from app.agents import prompts

from app.clients import embeddings as embeddings_client


from app.clients import llm_router


from app.gateways.dto import AutobiographyRecord, ChapterDraftCreateData, EventRecord

from app.gateways.factory import Gateways




async def generate_toc_candidates(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    from app.autobiography.constants import _TOC_GENERATION_MAX_EVENTS
    from app.autobiography.customization import _get_confirmed_customization
    from app.autobiography.text import _now_iso
    from app.autobiography.repository import get_autobiography_by_id
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)

    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    if not events:
        raise ValueError("목차를 생성하려면 먼저 Phase 3(consolidate_autobiography)이 완료되어야 합니다.")

    if len(events) > _TOC_GENERATION_MAX_EVENTS:
        events = sorted(
            events, key=lambda e: e.importance_score or Decimal(0), reverse=True
        )[:_TOC_GENERATION_MAX_EVENTS]

    summaries_block = "\n".join(
        f"- [중요도 {event.importance_score}] {event.one_line_summary} "
        f"(시기: {event.occurred_at_label or '미상'}, 감정: {event.emotion_tag or '미상'})"
        for event in events
    )

    # 커스터마이징이 확정돼 있으면 사용자가 선택한 구성(structure) 지시문을 주입한다.
    confirmed = _get_confirmed_customization(autobiography)
    if confirmed:
        toc_messages = prompts.build_customized_toc_prompt(
            event_summaries_with_scores=summaries_block,
            structure_key=confirmed["structure"],
        )
    else:
        toc_messages = prompts.build_toc_generation_prompt(
            event_summaries_with_scores=summaries_block
        )

    result_json = await llm_router.structured_completion(
        toc_messages,
        schema_name="toc_generation",
        json_schema=prompts.TOC_GENERATION_SCHEMA,
        reasoning_effort="medium",
    )
    toc_data = {
        "generated_at": _now_iso(),
        "candidates": [_normalize_toc_parts(c) for c in result_json["candidates"]],
        "selected_candidate_index": None,
    }
    autobiography = await gateways.autobiographies.update(autobiography_id, toc_data=toc_data)
    await gateways.commit()
    return autobiography


def _strip_part_title_prefix(part_title: str) -> str:
    from app.autobiography.constants import _PART_TITLE_PREFIX_PATTERN
    stripped = _PART_TITLE_PREFIX_PATTERN.sub("", part_title, count=1).strip()
    return stripped or part_title  # 접두어 제거 후 빈 문자열이면(전부 접두어였던 극단 케이스) 원본 유지


def _normalize_toc_parts(candidate: dict) -> dict:
    """TOC 후보 하나를 저장 전 결정론적으로 보정한다:
    1. 각 Part 제목에서 중복 번호 접두어를 제거한다(_strip_part_title_prefix).
    2. 챕터가 3개 미만인 Part를 인접 Part(첫 Part면 다음 Part, 그 외엔 이전
       Part)에 흡수시켜 제거하고, 남은 Part/챕터의 part_index를 1부터 다시
       연속 번호로 매긴다.

    Part가 1개 이하(episodic 예외 등 실제로 Part 구조가 없는 경우)면 제목
    접두어 제거만 하고 병합 로직은 건드리지 않는다 — 병합 대상 자체가 없다."""
    from app.autobiography.constants import _MIN_CHAPTERS_PER_PART
    candidate = dict(candidate)
    parts = [dict(p) for p in candidate.get("parts") or []]
    chapters = [dict(c) for c in candidate.get("chapters") or []]

    for part in parts:
        part["part_title"] = _strip_part_title_prefix(part["part_title"])

    if len(parts) <= 1:
        candidate["parts"] = parts
        candidate["chapters"] = chapters
        return candidate

    parts.sort(key=lambda p: p["part_index"])

    def _chapter_count(part_index: int) -> int:
        return sum(1 for c in chapters if c.get("part_index") == part_index)

    while len(parts) > 1:
        violating = next((p for p in parts if _chapter_count(p["part_index"]) < _MIN_CHAPTERS_PER_PART), None)
        if violating is None:
            break
        position = parts.index(violating)
        target = parts[position - 1] if position > 0 else parts[position + 1]
        for chapter in chapters:
            if chapter.get("part_index") == violating["part_index"]:
                chapter["part_index"] = target["part_index"]
        parts.remove(violating)

    renumber = {part["part_index"]: new_index for new_index, part in enumerate(parts, start=1)}
    for part in parts:
        part["part_index"] = renumber[part["part_index"]]
    for chapter in chapters:
        if chapter.get("part_index") in renumber:
            chapter["part_index"] = renumber[chapter["part_index"]]

    candidate["parts"] = parts
    candidate["chapters"] = chapters
    return candidate


async def select_toc_candidate(
    gateways: Gateways, autobiography_id: uuid.UUID, candidate_index: int
) -> AutobiographyRecord:
    from app.autobiography.retrieval import _assign_events_to_chapters
    from app.autobiography.retrieval import _chapter_time_scope
    from app.autobiography.writing import _generate_chapter_synopsis
    from app.autobiography.retrieval import _rebalance_assignment_by_year
    from app.autobiography.retrieval import _retrieve_events_for_chapter
    from app.autobiography.repository import get_autobiography_by_id
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    if not autobiography.toc_data or not autobiography.toc_data.get("candidates"):
        raise ValueError("먼저 목차 후보를 생성해야 합니다(generate_toc_candidates).")

    candidates = autobiography.toc_data["candidates"]
    if not (0 <= candidate_index < len(candidates)):
        raise ValueError(f"candidate_index={candidate_index}가 후보 범위를 벗어났습니다(총 {len(candidates)}개).")

    chosen = candidates[candidate_index]

    book_synopsis = await _generate_book_synopsis(autobiography, chosen)
    title = await _generate_book_title(autobiography, chosen)

    # 챕터 시놉시스를 목차 확정 시점에 전 챕터 분량으로 미리 생성해 초안에 함께
    # 저장한다. 두 가지를 동시에 얻는다(2026-07-17): (1) 챕터당 ~20초짜리 최대
    # 단일 구간이 집필 임계 경로에서 빠지고, (2) write_chapter의 "직전 챕터 요약"이
    # 직전 챕터의 완성 본문 대신 이 시놉시스를 읽게 되어 챕터 간 직렬 의존이
    # 사라진다 — 프론트는 이미 전 챕터를 Promise.all로 동시에 큐잉하므로, 이
    # 변경으로 전체 집필 시간이 챕터 수와 무관해진다(워커 동시성 범위 내).
    # 이벤트 검색은 같은 DB 세션을 공유하므로 순차로 돌고(챕터당 ~1.6초, 대부분
    # 임베딩 HTTP), LLM 시놉시스 호출만 asyncio.gather로 병렬화한다.
    #
    # part_synopses 생성보다 먼저 실행한다 — Part 시놉시스도 이 검색 결과를
    # 근거로 받아야 한다(아래 _generate_part_synopses 참조). 예전엔 part_synopsis가
    # 사건 자료를 하나도 못 보고 book_synopsis·챕터 제목만 보고 지어냈는데, 이
    # 때문에 "옥스퍼드에서 태어났다"는 근거를 "도서관에서 태어났다"로 구체화해
    # 지어내는 환각이 실사용 중 확인됐다(2026-07-17) — 새 검색 호출 없이 이미
    # 여기서 구하는 chapter_events를 재사용해 근거를 붙여준다.
    # 임베딩 HTTP 호출은 서로 독립이라 전 챕터 분량을 병렬로 먼저 구하고, DB 검색만
    # 같은 세션을 공유하는 제약 때문에 순차로 돈다(2026-07-18 — 이전엔 임베딩까지
    # 챕터당 순차 실행이라 19장 기준 임계 경로가 ~30초씩 걸렸다).
    chapter_titles = [chapter_data.get("title") or "" for chapter_data in chosen["chapters"]]
    embedded_vectors = iter(
        await asyncio.gather(
            *(embeddings_client.embed_query(title) for title in chapter_titles if title)
        )
    )
    chapter_vectors = [next(embedded_vectors) if title else None for title in chapter_titles]

    chapter_events: list[list[EventRecord]] = []
    # 주의: 루프 변수를 title로 쓰면 위의 책 제목(title)을 가려버린다 — 실제로
    # 책 제목이 마지막 챕터 제목으로 저장되는 회귀를 테스트가 잡아냈다(2026-07-18).
    for chapter_title, vector in zip(chapter_titles, chapter_vectors):
        chapter_events.append(
            await _retrieve_events_for_chapter(
                gateways, autobiography.user_id, chapter_title, query_vector=vector
            )
        )

    # 배타적 배정: 같은 사건이 여러 챕터의 검색 결과에 들어 있으면 가장 관련도
    # 높은 챕터 하나에만 남긴다 — 이 배정 결과가 시놉시스 생성과 ChapterDraft
    # 저장(source_event_ids)에 함께 쓰여, 이후 write_chapter의 집필 재료와
    # 시놉시스의 설계 재료가 항상 일치한다. 이어서 시기 정합 보정: 검색 순위가
    # 시기를 무시하고 엉뚱한 챕터에 배정한 사건을 연도 중앙값 기준으로 재배치.
    retrieved_chapter_events = chapter_events
    chapter_events = _assign_events_to_chapters(retrieved_chapter_events)
    chapter_events = _rebalance_assignment_by_year(chapter_events, retrieved_chapter_events)

    part_synopses = await _generate_part_synopses(book_synopsis, chosen, chapter_events)
    if part_synopses:
        chosen = {
            **chosen,
            "parts": [
                {**part, "part_synopsis": part_synopses.get(part["part_index"], part["part_arc"])}
                for part in chosen["parts"]
            ],
        }
        candidates = [chosen if i == candidate_index else c for i, c in enumerate(candidates)]

    chapter_synopses = await asyncio.gather(
        *(
            _generate_chapter_synopsis(
                book_synopsis=book_synopsis,
                chapter_title=chapter_data.get("title") or f"{chapter_data['chapter_index']}장",
                event_summaries=[event.one_line_summary for event in events],
                connecting_thread=chapter_data.get("connecting_thread"),
                part_context=_part_context_from_selected(chosen, chapter_data["chapter_index"]),
                time_scope=_chapter_time_scope(events),
            )
            for chapter_data, events in zip(chosen["chapters"], chapter_events)
        )
    )

    # 재선택 시 이전 챕터 초안을 대체한다(idempotent).
    await gateways.chapters.replace_all(
        autobiography.id,
        [
            ChapterDraftCreateData(
                chapter_index=chapter_data["chapter_index"],
                title=chapter_data["title"],
                synopsis=synopsis,
                source_event_ids=[event.id for event in events],
            )
            for chapter_data, synopsis, events in zip(
                chosen["chapters"], chapter_synopses, chapter_events
            )
        ],
    )

    updated_toc = {**autobiography.toc_data, "candidates": candidates, "selected_candidate_index": candidate_index}

    autobiography = await gateways.autobiographies.update(
        autobiography_id, toc_data=updated_toc, book_synopsis=book_synopsis, title=title
    )
    await gateways.commit()
    return autobiography


async def _generate_part_synopses(
    book_synopsis: str, chosen: dict, chapter_events: list[list[EventRecord]]
) -> dict[int, str]:
    """chosen['parts']의 씨앗 part_arc를 book_synopsis 확정 후 더 풍부한 Part
    시놉시스로 확장한다. Part가 1개 이하(episodic 예외/비커스터마이징 폴백)면
    확장할 실익이 없어 빈 dict를 반환한다.

    chapter_events: chosen["chapters"]와 같은 순서로, 각 챕터가 select_toc_
    candidate에서 이미 검색해 둔 사건 목록 — 여기서 새로 검색하지 않고 Part별로
    묶어(같은 사건이 그 Part의 여러 챕터에서 검색되면 event.id로 중복 제거)
    Part 시놉시스 프롬프트에 실제 근거로 넘긴다. 예전엔 이 함수가 book_synopsis·
    챕터 제목만 보고 Part 시놉시스를 지어냈는데, 실제 사건 자료가 전혀 없다 보니
    "옥스퍼드에서 태어났다"는 근거를 "도서관 복도에서 태어났다"로 구체화해
    지어내는 환각이 실사용 중 확인됐다(2026-07-17) — 그 수정."""
    parts = chosen.get("parts") or []
    if len(parts) <= 1:
        return {}

    chapters_by_part: dict[int, list[str]] = {}
    events_by_part: dict[int, dict[uuid.UUID, str]] = {}
    for chapter_data, events in zip(chosen["chapters"], chapter_events):
        part_index = chapter_data["part_index"]
        chapters_by_part.setdefault(part_index, []).append(chapter_data["title"])
        bucket = events_by_part.setdefault(part_index, {})
        for event in events:
            bucket[event.id] = event.one_line_summary

    part_synopses: dict[int, str] = {}
    for part in parts:
        # max_tokens를 명시하지 않으면 API 서버 기본값에 맡기게 되는데, 실측 중
        # (2026-07-17) Part 4개 중 마지막 호출 하나가 실제 시놉시스 대신 모델의
        # 계획 서술("The user wants us to create...")만 담긴 응답을 반환한 사고가
        # 있었다 — reasoning_effort="high"가 max_tokens를 추론 토큰만으로
        # 소진해버린 챕터 집필 사고와 같은 계열의 문제로 보인다. 넉넉한 여유를 둔다.
        response = await llm_router.chat_completion(
            prompts.build_part_synopsis_prompt(
                book_synopsis=book_synopsis,
                part_title=part["part_title"],
                part_arc_seed=part["part_arc"],
                chapter_titles=chapters_by_part.get(part["part_index"], []),
                event_summaries=list(events_by_part.get(part["part_index"], {}).values()),
            ),
            reasoning_effort="low",
            max_tokens=4000,
        )
        part_synopses[part["part_index"]] = response.choices[0].message.content or part["part_arc"]
    return part_synopses


def _toc_text(selected_toc: dict) -> str:
    arc = selected_toc.get("narrative_arc")
    arc_block = f"[전체 뼈대]\n{arc}\n\n" if arc else ""
    chapters = selected_toc["chapters"]
    parts = selected_toc.get("parts") or []

    def _chapter_line(chapter: dict) -> str:
        return (
            f"{chapter['chapter_index']}. {chapter['title']} "
            f"({', '.join(chapter.get('theme_keywords', []))})"
            + (f" — 연결고리: {chapter['connecting_thread']}" if chapter.get("connecting_thread") else "")
        )

    # Part가 2개 이상일 때만 Part별로 묶어 렌더링한다 — episodic 예외(Part 1개)나
    # Part 필드 자체가 없는 구버전 toc_data는 기존과 동일한 평평한 렌더링으로
    # 폴백해야 하위 호환이 깨지지 않는다.
    if len(parts) > 1:
        parts_by_index = {part["part_index"]: part for part in parts}
        lines: list[str] = []
        current_part_index: int | None = None
        for chapter in chapters:
            part_index = chapter.get("part_index")
            if part_index != current_part_index:
                current_part_index = part_index
                part = parts_by_index.get(part_index)
                if part:
                    lines.append(f"\n[{part['part_index']}부. {part['part_title']}] — {part.get('part_arc', '')}")
            lines.append(f"  {_chapter_line(chapter)}")
        chapters_text = "\n".join(lines)
    else:
        chapters_text = "\n".join(_chapter_line(chapter) for chapter in chapters)

    return f"{arc_block}{chapters_text}"


def get_chapter_part_context(autobiography: AutobiographyRecord, chapter_index: int) -> dict | None:
    """toc_data에 저장된 선택 후보에서 이 챕터가 속한 Part의 컨텍스트(제목·
    시놉시스·Part 경계 여부·인접 Part 제목)를 찾는다. _chapter_connecting_thread와
    동일한 패턴(DB 컬럼 없이 toc_data JSON만 읽음) — pdf_service.py에서도 쓰이므로
    공개 함수로 둔다. toc_data/선택 후보가 없거나, parts가 1개 이하(episodic 예외
    또는 비커스터마이징 폴백), 이 chapter_index를 찾지 못했거나 part_index가 없는
    구버전 toc_data라면 None을 반환해 "Part 구조 없음"으로 취급한다."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return None
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return None
    return _part_context_from_selected(candidates[selected_index], chapter_index)


def _part_context_from_selected(selected: dict, chapter_index: int) -> dict | None:
    """get_chapter_part_context의 본체 — 선택된 목차 후보 dict를 직접 받는 변형.
    select_toc_candidate는 toc_data에 selected_candidate_index를 저장하기 전에
    (챕터 시놉시스 사전 생성 시점) Part 컨텍스트가 필요하므로 이 형태로 분리했다."""
    parts = selected.get("parts") or []
    if len(parts) <= 1:
        return None
    parts_by_index = {part["part_index"]: part for part in parts}
    chapters = selected.get("chapters", [])

    target = next((c for c in chapters if c.get("chapter_index") == chapter_index), None)
    if target is None or target.get("part_index") is None:
        return None
    part_index = target["part_index"]
    part = parts_by_index.get(part_index)
    if part is None:
        return None

    siblings = sorted(c["chapter_index"] for c in chapters if c.get("part_index") == part_index)
    ordered_part_indices = sorted(parts_by_index)
    pos = ordered_part_indices.index(part_index)

    return {
        "part_index": part_index,
        "part_title": part["part_title"],
        "part_synopsis": part.get("part_synopsis") or part.get("part_arc", ""),
        "is_part_opening": chapter_index == siblings[0],
        "is_part_closing": chapter_index == siblings[-1],
        "prev_part_title": (
            parts_by_index[ordered_part_indices[pos - 1]]["part_title"] if pos > 0 else None
        ),
        "next_part_title": (
            parts_by_index[ordered_part_indices[pos + 1]]["part_title"]
            if pos < len(ordered_part_indices) - 1
            else None
        ),
    }


def get_ordered_parts(autobiography: AutobiographyRecord) -> list[dict]:
    """선택된 후보의 parts를 part_index 오름차순으로 반환한다. Part 구조가 없으면
    (없음 또는 1개 이하) 빈 리스트 — pdf_service/프론트는 이를 "Part UI를 아예
    숨겨라"는 신호로 쓴다."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return []
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return []
    parts = candidates[selected_index].get("parts") or []
    if len(parts) <= 1:
        return []
    return sorted(parts, key=lambda part: part["part_index"])


def _chapter_connecting_thread(autobiography: AutobiographyRecord, chapter_index: int) -> str | None:
    """toc_data에 저장된 선택된 후보에서 이 챕터의 connecting_thread(목차 설계
    단계에서 정해진, 직전/다음 챕터와의 연결고리)를 찾는다. DB 스키마 변경 없이
    이미 저장된 toc_data(자유 JSON) 안의 값을 읽기만 한다 — 커스터마이징 이전에
    생성된 목차(connecting_thread 필드가 없는 구버전)라면 None."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return None
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return None
    for chapter in candidates[selected_index].get("chapters", []):
        if chapter.get("chapter_index") == chapter_index:
            return chapter.get("connecting_thread")
    return None


async def _generate_book_synopsis(autobiography: AutobiographyRecord, selected_toc: dict) -> str:
    from app.autobiography.text import _strip_synopsis_markdown_header
    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    response = await llm_router.chat_completion(
        prompts.build_book_synopsis_prompt(style_bible=style_bible_text, toc=_toc_text(selected_toc)),
        reasoning_effort="medium",
    )
    return _strip_synopsis_markdown_header(response.choices[0].message.content or "")


async def _generate_book_title(autobiography: AutobiographyRecord, selected_toc: dict) -> str:
    """표지·PDF 조판(app/services/pdf_service.py)에 그대로 노출되는 책 제목.
    toc/select 이전에는 Autobiography.title이 채워질 방법이 아예 없었다 — 목차가
    확정돼야 비로소 책 전체를 관통하는 제목을 지을 컨텍스트(스타일 바이블 + 목차)가
    갖춰지므로, book_synopsis와 같은 시점에 함께 생성한다."""
    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    result = await llm_router.structured_completion(
        prompts.build_book_title_prompt(style_bible=style_bible_text, toc=_toc_text(selected_toc)),
        schema_name="book_title",
        json_schema=prompts.BOOK_TITLE_SCHEMA,
        reasoning_effort="low",
    )
    return result["title"].strip()
