"""Writing domain operations."""
from __future__ import annotations


import asyncio



import re


import uuid



from app.agents import prompts



from app.clients import llm_router


from app.gateways.dto import ChapterDraftRecord, ChapterDraftWriteResult, EventRecord

from app.gateways.factory import Gateways

from app.models.enums import DraftStatus

from app.services import character_service


def _total_flag_count(factcheck_report: dict, groundedness_report: dict) -> int:
    return len(factcheck_report.get("flags", [])) + len(groundedness_report.get("flags", []))


def _source_events_text(source_events: list[EventRecord]) -> str:
    return "\n".join(
        f"- {event.prose_paragraph}"
        for event in source_events
        if event.prose_paragraph and event.prose_paragraph.strip()
    )


def _strip_citation_tags(content: str, event_count: int) -> tuple[str, list[str]]:
    """집필 프롬프트의 근거 태그([E1]...)를 본문에서 제거하고, 유효한 태그가 하나도
    없는 문단 목록을 함께 반환한다(근거검증 판정자의 집중 검토 대상 — 순수 전환·감상
    문단일 수도 있으므로 자동 플래그가 아니라 '주의 지목'까지만 한다). 유효 태그 =
    1..event_count 범위의 사건 번호. 태그는 조판 전에 반드시 제거되어야 한다 —
    PDF에 그대로 인쇄되면 안 된다."""
    from app.autobiography.constants import _CITATION_TAG_PATTERN
    uncited_paragraphs: list[str] = []
    cleaned_paragraphs: list[str] = []
    for paragraph in content.split("\n\n"):
        cited_numbers = [int(n) for n in _CITATION_TAG_PATTERN.findall(paragraph)]
        cleaned = _CITATION_TAG_PATTERN.sub("", paragraph)
        # 태그 제거 자리에 남는 이중 공백/문장부호 앞 공백만 정돈한다(줄바꿈 유지).
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r" +([.,!?])", r"\1", cleaned).strip()
        if not cleaned:
            continue
        cleaned_paragraphs.append(cleaned)
        if not any(1 <= n <= event_count for n in cited_numbers):
            uncited_paragraphs.append(cleaned)
    return "\n\n".join(cleaned_paragraphs), uncited_paragraphs


def _flagged_items_for_repair(factcheck_report: dict, groundedness_report: dict) -> list[dict[str, str]]:
    """팩트체크 플래그(fact_type/raw_text)와 근거검증 플래그(sentence/reason)를
    수리 프롬프트가 받는 공통 모양({"sentence", "reason"})으로 정규화한다."""
    items: list[dict[str, str]] = []
    for flag in factcheck_report.get("flags", []):
        items.append(
            {
                "sentence": flag["raw_text"],
                "reason": f"본문 속 {flag['fact_type']} 표현이 근거 사건 어디에도 없음",
            }
        )
    for flag in groundedness_report.get("flags", []):
        items.append({"sentence": flag["sentence"], "reason": flag["reason"]})
    return items


async def _repair_chapter_content(
    *,
    chapter_content: str,
    factcheck_report: dict,
    groundedness_report: dict,
    source_events_text: str,
) -> str:
    """플래그된 문장만 근거에 맞게 고치거나 삭제하는 외과적 수리 호출. 전체
    재집필(reasoning_effort="high" + 5,500~7,000자 생성)보다 훨씬 싸고, 멀쩡한
    부분에 새 환각이 생길 위험이 없다. max_tokens는 챕터 전체를 되돌려주는
    호출이라 분량 지시 상향(2026-07-19) 이후의 챕터 길이에 맞춰
    _generate_chapter_content와 동일하게 둔다(수리 과정에서 잘리지 않도록)."""
    messages = prompts.build_chapter_repair_prompt(
        chapter_content=chapter_content,
        flagged_items=_flagged_items_for_repair(factcheck_report, groundedness_report),
        source_events_text=source_events_text,
    )
    response = await llm_router.chat_completion(messages, reasoning_effort="medium", max_tokens=24000)
    return response.choices[0].message.content or ""


async def write_chapter(gateways: Gateways, chapter_draft_id: uuid.UUID) -> ChapterDraftRecord:
    """
    Phase 4 하향식 집필의 챕터 단위 실행: [하이브리드 RAG 소환 → 본문 집필(근거 태그
    포함) → 팩트체크·근거검증(병렬) → 플래그 시 외과적 수리 루프 → 등장인물 스캔].

    챕터 시놉시스는 select_toc_candidate가 목차 확정 시점에 전 챕터 분량을 병렬로
    미리 생성해 두므로(챕터당 ~20초의 최대 단일 구간이 임계 경로에서 빠진다) 여기서는
    저장된 값을 읽기만 한다 — 구버전 초안(시놉시스 없음)만 폴백으로 즉석 생성한다.
    """
    from app.autobiography.constants import MAX_REPAIR_PASSES
    from app.autobiography.constants import PROOFREAD_MAX_LENGTH_DRIFT
    from app.autobiography.toc import _chapter_connecting_thread
    from app.autobiography.retrieval import _chapter_time_scope
    from app.autobiography.text import _count_overused_terms
    from app.autobiography.customization import _get_confirmed_customization
    from app.autobiography.publishing import _join_chapters_into_final_content
    from app.autobiography.retrieval import _other_chapter_titles
    from app.autobiography.retrieval import _previous_chapter_summary
    from app.autobiography.retrieval import _retrieve_events_for_chapter
    from app.autobiography.verification import _run_factcheck
    from app.autobiography.verification import _run_groundedness_check
    from app.autobiography.retrieval import _sort_events_chronologically
    from app.autobiography.text import _strip_prompt_section_echo
    from app.autobiography.toc import get_chapter_part_context
    from app.autobiography.constants import logger
    chapter = await gateways.chapters.get(chapter_draft_id)
    if chapter is None:
        raise ValueError(f"ChapterDraft {chapter_draft_id} not found")
    autobiography = await gateways.autobiographies.get_by_id(chapter.autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {chapter.autobiography_id} not found")
    if not autobiography.book_synopsis:
        raise ValueError("먼저 목차를 선택해 책 전체 시놉시스를 생성해야 합니다(select_toc_candidate).")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")

    # select_toc_candidate가 배타적 배정으로 저장해 둔 사건이 있으면 그대로 쓴다
    # (재검색하면 다른 챕터에 배정된 사건이 다시 섞여 들어와 배정이 무의미해진다).
    # 빈 리스트 = 배정 이전 구버전 초안 — 기존 하이브리드 검색으로 폴백.
    if chapter.source_event_ids:
        retrieved_events = await gateways.events.list_by_ids(chapter.source_event_ids)
    else:
        retrieved_events = await _retrieve_events_for_chapter(
            gateways, autobiography.user_id, chapter.title or ""
        )
    # list_by_ids/_retrieve_events_for_chapter는 중요도·검색 순위로 정렬돼 있어
    # (EventGateway.list_by_ids의 ORDER BY importance_score), 그대로 집필
    # 프롬프트에 넘기면 시간 순서가 뒤죽박죽된 채 그대로 서술된다(2026-07-19
    # 실사용 중 확인 — 1965년 결혼 다음 문단에 17세 졸업이 나오는 등). LLM에게
    # "시간순으로 재배열하라"고 지시하는 것보다 코드에서 확정적으로 정렬하는
    # 편이 훨씬 안정적이다.
    retrieved_events = _sort_events_chronologically(retrieved_events)
    source_event_ids = [event.id for event in retrieved_events]
    time_scope = _chapter_time_scope(retrieved_events)
    other_titles = _other_chapter_titles(autobiography, chapter.chapter_index)

    chapter_synopsis = chapter.chapter_synopsis or await _generate_chapter_synopsis(
        book_synopsis=autobiography.book_synopsis,
        chapter_title=chapter.title or f"{chapter.chapter_index}장",
        event_summaries=[event.one_line_summary for event in retrieved_events],
        connecting_thread=_chapter_connecting_thread(autobiography, chapter.chapter_index),
        part_context=get_chapter_part_context(autobiography, chapter.chapter_index),
        time_scope=time_scope,
    )

    previous_summary = await _previous_chapter_summary(gateways, autobiography.id, chapter.chapter_index)

    # 커스터마이징이 확정돼 있으면 말투·컨셉 지시문을 챕터 집필에 주입한다.
    confirmed = _get_confirmed_customization(autobiography)
    content = await _generate_chapter_content(
        style_bible=style_bible_text,
        book_synopsis=autobiography.book_synopsis,
        chapter_synopsis=chapter_synopsis,
        previous_chapter_summary=previous_summary,
        retrieved_event_paragraphs=[event.prose_paragraph for event in retrieved_events],
        tone_key=confirmed["tone"] if confirmed else None,
        concept_key=confirmed["concept"] if confirmed else None,
        time_scope=time_scope,
        other_chapter_titles=other_titles,
    )
    if not content.strip():
        # 본문이 비어 있으면(예: reasoning_effort="high"가 max_tokens 예산을 추론
        # 토큰만으로 다 써버려 실제 본문을 못 받은 경우, 2026-07-17 실측) 검증도
        # 수리도 대상이 없다 — 같은 자료로 1회만 재집필을 시도한다. 빈 본문은
        # factcheck/groundedness 둘 다 "검증 대상 없음"으로 조용히 통과하므로
        # 아래 플래그 기반 수리 루프로는 이 실패를 못 잡는다.
        content = await _generate_chapter_content(
            style_bible=style_bible_text,
            book_synopsis=autobiography.book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_summary,
            retrieved_event_paragraphs=[event.prose_paragraph for event in retrieved_events],
            tone_key=confirmed["tone"] if confirmed else None,
            concept_key=confirmed["concept"] if confirmed else None,
            time_scope=time_scope,
            other_chapter_titles=other_titles,
        )

    # 분량 확장 패스는 폐기됐다(2026-07-19, prompts.py의 폐기 사유 주석 참조) —
    # "원본보다 길어지기만 하면 채택"이라는 약한 기준으로는 3,000자 문턱조차
    # 보장 못 하면서, 챕터당 순차 Solar 호출을 1회 더 얹어 처리 시간만 늘렸다
    # (집필 5~10분/챕터 실측). 대신 CHAPTER_WRITING_SYSTEM_PROMPT의 분량 지시
    # 자체를 장면 수·문단 수·문장 수 기준으로 바꿔 첫 호출에서 채우도록 했다.

    # 근거 태그([E1]...) 회수 — 태그가 하나도 없는 문단은 근거검증 판정자의 집중
    # 검토 대상으로 지목한다(집필 규약상 사실 서술 문단은 반드시 태그를 달아야 한다).
    content, uncited_paragraphs = _strip_citation_tags(content, len(retrieved_events))

    narrator = await gateways.users.get_by_id(autobiography.user_id)
    birth_year = narrator.birth_year if narrator else None
    # 팩트체크와 근거검증은 서로 독립적인 LLM 호출이라 병렬로 돌린다.
    factcheck_report, groundedness_report = await asyncio.gather(
        _run_factcheck(content, source_events=retrieved_events, birth_year=birth_year),
        _run_groundedness_check(
            content, source_events=retrieved_events, attention_paragraphs=uncited_paragraphs
        ),
    )

    # 외과적 수리 루프: 플래그가 남아 있으면 플래그된 문장만 고치는 수리 호출을
    # 최대 MAX_REPAIR_PASSES회 반복한다. 예전의 블라인드 전체 재집필(플래그 내용을
    # 전달하지 않고 같은 프롬프트로 다시 쓰기)은 수렴 보장이 없고 멀쩡한 부분에
    # 새 환각이 생길 수 있어 교체했다(2026-07-17). 수리 결과는 플래그가 실제로
    # 줄었을 때만 채택한다 — 줄지 않으면 더 반복해도 개선 가능성이 낮아 중단하고,
    # 잔여 플래그를 리포트에 남긴다(검토 화면에서 확인 가능).
    events_text = _source_events_text(retrieved_events)
    for _ in range(MAX_REPAIR_PASSES):
        if _total_flag_count(factcheck_report, groundedness_report) == 0:
            break
        repaired = await _repair_chapter_content(
            chapter_content=content,
            factcheck_report=factcheck_report,
            groundedness_report=groundedness_report,
            source_events_text=events_text,
        )
        repaired, _ = _strip_citation_tags(repaired, len(retrieved_events))
        if not repaired.strip():
            break
        repaired_factcheck, repaired_groundedness = await asyncio.gather(
            _run_factcheck(repaired, source_events=retrieved_events, birth_year=birth_year),
            _run_groundedness_check(repaired, source_events=retrieved_events),
        )
        if _total_flag_count(repaired_factcheck, repaired_groundedness) >= _total_flag_count(
            factcheck_report, groundedness_report
        ):
            break
        content, factcheck_report, groundedness_report = (
            repaired,
            repaired_factcheck,
            repaired_groundedness,
        )

    # 검수(교열) 패스: 오탈자·비문("속술했다"), 1인칭 이탈(3인칭 혼입), 종결어미
    # 격식 혼입("-습니다" 붕괴), 표현 남발, 시대착오적 묘사를 챕터 단위에서 고친다
    # (2026-07-18 호킹 계정 실측 결함들 — 최종 통일성 윤문(finalize)만으로는 검토
    # 화면에서 사용자가 읽는 윤문 전 본문에 그대로 노출된다). 사실 관계·문단
    # 구조는 건드리지 않는 교열 전용 프롬프트라 팩트체크 리포트는 재실행하지
    # 않는다. 빈 응답이거나 길이가 ±30% 넘게 변하면(교열이 아니라 개고를 한
    # 것) 원본을 유지한다.
    if content.strip():
        proofread_response = await llm_router.chat_completion(
            prompts.build_chapter_proofread_prompt(
                chapter_content=content,
                overused_terms=_count_overused_terms(content),
            ),
            reasoning_effort="low",
            # 챕터 전체를 되돌려주는 호출이라 분량 지시 상향(2026-07-19) 이후의
            # 챕터 길이에 맞춰 _generate_chapter_content와 동일하게 둔다.
            max_tokens=24000,
        )
        proofread = _strip_prompt_section_echo(
            proofread_response.choices[0].message.content or "", body_marker="[챕터 본문]"
        )
        if proofread and abs(len(proofread) - len(content)) <= len(content) * PROOFREAD_MAX_LENGTH_DRIFT:
            content = proofread
        elif proofread:
            logger.warning(
                "챕터 검수 패스 결과 길이가 원본 대비 30%%를 초과해 변해 원본 유지(원본 %d자 → 검수 %d자)",
                len(content),
                len(proofread),
            )

    chapter = await gateways.chapters.save_write_result(
        chapter_draft_id,
        ChapterDraftWriteResult(
            source_event_ids=source_event_ids,
            chapter_synopsis=chapter_synopsis,
            content=content,
            factcheck_report=factcheck_report,
            groundedness_report=groundedness_report,
            status=DraftStatus.REVIEWED,
        ),
    )
    await character_service.scan_and_classify_chapter(gateways, chapter=chapter, autobiography=autobiography)

    # 이미 최종 윤문이 끝난 책(final_content 존재)에서 사용자가 개별 챕터를 다시
    # 쓰면("이 챕터 다시 쓰기", 완성 후에도 유지되는 기능) final_content도 같이
    # 새로 조립한다 — 안 그러면 웹에서 보는 최종본만 옛 본문에 머문다(PDF는
    # chapter.content를 직접 읽으므로 원래 영향 없음, pdf_service 참조).
    if autobiography.final_content is not None:
        all_chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
        refreshed_final_content = _join_chapters_into_final_content(all_chapters)
        await gateways.autobiographies.update(autobiography.id, final_content=refreshed_final_content)

    await gateways.commit()
    return chapter


async def _generate_chapter_synopsis(
    *,
    book_synopsis: str,
    chapter_title: str,
    event_summaries: list[str],
    connecting_thread: str | None = None,
    part_context: dict | None = None,
    time_scope: str | None = None,
) -> str:
    response = await llm_router.chat_completion(
        prompts.build_chapter_synopsis_prompt(
            book_synopsis=book_synopsis,
            chapter_title=chapter_title,
            event_summaries=event_summaries,
            connecting_thread=connecting_thread,
            part_context=part_context,
            time_scope=time_scope,
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
    tone_key: str | None = None,
    concept_key: str | None = None,
    time_scope: str | None = None,
    other_chapter_titles: list[str] | None = None,
) -> str:
    if tone_key and concept_key:
        messages = prompts.build_customized_chapter_writing_prompt(
            style_bible=style_bible,
            book_synopsis=book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_chapter_summary,
            retrieved_event_paragraphs=retrieved_event_paragraphs,
            tone_key=tone_key,
            concept_key=concept_key,
            time_scope=time_scope,
            other_chapter_titles=other_chapter_titles,
        )
    else:
        messages = prompts.build_chapter_writing_prompt(
            style_bible=style_bible,
            book_synopsis=book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_chapter_summary,
            retrieved_event_paragraphs=retrieved_event_paragraphs,
            time_scope=time_scope,
            other_chapter_titles=other_chapter_titles,
        )
    # max_tokens=8000으로 처음 지정했다가 실사용 검증 중 본문이 통째로 빈
    # 문자열로 나오는 사고가 있었다(2026-07-17) — reasoning_effort="high"는
    # 눈에 보이지 않는 "추론 토큰"을 먼저 소비하고 그것도 max_tokens에
    # 포함되는데, 복잡한 챕터 프롬프트(전체 시놉시스+챕터 시놉시스+직전 챕터
    # 레시피+사건 문단 최대 20개)에서는 추론 토큰만으로 8000을 다 써버려
    # 실제 본문을 한 글자도 못 받는 경우가 실측됐다. 분량 지시 상향(2026-07-19,
    # 장면 6~7개·장면당 문단 8개 이상·문단당 4문장 이상 — 목표 약 5,500~7,000자)
    # 이후에는 추론 토큰 여유를 그만큼 더 넉넉히 둬야 같은 사고가 재현되지 않는다.
    response = await llm_router.chat_completion(messages, reasoning_effort="high", max_tokens=24000)
    return response.choices[0].message.content or ""
