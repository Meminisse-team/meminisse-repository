"""Publishing domain operations."""
from __future__ import annotations







import uuid



from app.agents import prompts



from app.clients import llm_router


from app.gateways.dto import AutobiographyRecord, ChapterDraftRecord

from app.gateways.factory import Gateways

from app.models.enums import AutobiographyStatus, UserStage



def _split_revised_manuscript_by_chapter(
    revised: str, chapter_indexes: list[int]
) -> dict[int, str] | None:
    """윤문 응답을 `<<<CHAPTER n>>>` 마커로 챕터별 본문으로 나눈다. 마커 집합이
    입력 챕터 목록과 정확히 일치하지 않으면(누락/중복/새 마커) None — 호출부가
    "윤문 전 챕터 유지 + 전문만 final_content" 폴백으로 처리한다.

    각 조각에서 챕터 헤더(`[N장. 제목]`) 줄과 PART 마커는 제거한다 — 제목·Part는
    별도 필드/toc_data로 관리되므로 chapter.content에는 순수 본문만 남아야
    PDF 조판(pdf_service)이 이중 표기 없이 그대로 쓸 수 있다."""
    from app.autobiography.constants import _CHAPTER_HEADER_PATTERN
    from app.autobiography.constants import _CHAPTER_MARKER_PATTERN
    from app.autobiography.constants import _PART_MARKER_PATTERN
    matches = list(_CHAPTER_MARKER_PATTERN.finditer(revised))
    found_indexes = [int(m.group(1)) for m in matches]
    if found_indexes != chapter_indexes:
        return None

    by_index: dict[int, str] = {}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(revised)
        chunk = revised[start:end]
        chunk = _PART_MARKER_PATTERN.sub("", chunk)
        chunk = _CHAPTER_HEADER_PATTERN.sub("", chunk, count=1)
        chunk = chunk.strip()
        if not chunk:
            return None
        by_index[found_indexes[i]] = chunk
    return by_index


def _join_chapters_into_final_content(chapters: list[ChapterDraftRecord]) -> str:
    """챕터별 본문을 book 전체 final_content로 조립하는 단일 진실 원천 — 최초
    윤문(finalize_manuscript)과 이후의 모든 챕터 단위 변경(직접 수정
    edit_chapter_content, 완성 후 AI 재집필 write_chapter)이 이 함수 하나로
    final_content를 다시 만든다. 형식이 두 곳 이상에 흩어져 있으면 한쪽만
    고쳤을 때 웹 열람과 PDF가 다시 어긋날 위험이 있어(2026-07-18에 고쳤던 바로
    그 문제) 반드시 이 헬퍼를 거치게 한다."""
    return "\n\n".join(
        f"[{chapter.chapter_index}장. {chapter.title}]\n{chapter.content}" for chapter in chapters
    )


async def edit_chapter_content(
    gateways: Gateways, autobiography_id: uuid.UUID, chapter_draft_id: uuid.UUID, content: str
) -> AutobiographyRecord:
    """작성된 챕터 본문을 사용자가 직접 고쳐 쓴다('나의 자서전' 직접 수정 기능).
    AI 재집필(write_chapter)과 달리 LLM·외부 API 호출이 전혀 없는 순수 텍스트
    저장이라 요청이 즉시 끝난다 — 느린 외부 호출을 기다리며 DB 세션을 오래
    붙잡다 Supabase가 idle 커넥션을 끊어버리는 문제(interview_service.
    add_user_turn 모듈 docstring 참조: 예전에 세션 대화 저장 경로에서 실제로
    겪었던 사고)가 애초에 발생할 여지가 없다 — 이 함수 전체가 조회 몇 번 + 텍스트
    조립 + 커밋 하나뿐이다.

    최종 윤문(finalize_manuscript) 이전 검토 단계에서도 호출된다(2026-07-20 —
    "확인 필요" 배지가 붙은 챕터를 그 자리에서 바로 고칠 수 있어야 함). 이때는
    final_content가 아직 없으므로(finalize를 아직 안 거침) 건드리지 않는다 —
    여기서 final_content를 채워버리면 finalize_manuscript가 하는 통일성 윤문
    (문체·종결어미 통일)을 건너뛴 채로 프론트가 "최종본이 나왔다"고 오인해
    자동으로 FinalManuscript 화면으로 넘어가버린다. final_content는 오직 이미
    한 번 최종본이 나온 뒤의 수정(재조립)에서만 갱신한다."""
    from app.autobiography.repository import get_chapter_draft
    chapter = await get_chapter_draft(gateways, chapter_draft_id)
    if chapter is None or chapter.autobiography_id != autobiography_id:
        raise ValueError(f"자서전 {autobiography_id}에 속한 챕터 {chapter_draft_id}를 찾을 수 없습니다.")

    await gateways.chapters.update_content(chapter_draft_id, content)

    autobiography = await gateways.autobiographies.get_by_id(autobiography_id)
    assert autobiography is not None
    if autobiography.final_content is not None:
        chapters = await gateways.chapters.list_by_autobiography(autobiography_id)
        final_content = _join_chapters_into_final_content(chapters)
        autobiography = await gateways.autobiographies.update(
            autobiography_id, final_content=final_content
        )
    await gateways.commit()
    return autobiography


def _group_chapters_for_finalize(
    autobiography: AutobiographyRecord, chapters: list[ChapterDraftRecord]
) -> list[list[ChapterDraftRecord]]:
    """finalize_manuscript의 통일성 윤문 호출을 배치로 나누기 위해 챕터를
    묶는다. Part 경계를 절대 넘지 않고(같은 배치에 서로 다른 Part의 챕터가
    섞이지 않음), 한 Part가 _FINALIZE_BATCH_MAX_CHAPTERS를 넘으면 그 안에서
    순서대로 다시 쪼갠다. Part 구조가 아예 없으면(get_chapter_part_context가
    모든 챕터에 None을 반환) 전체를 하나의 그룹으로 보고 동일하게 나눈다."""
    from app.autobiography.constants import _FINALIZE_BATCH_MAX_CHAPTERS
    from app.autobiography.toc import get_chapter_part_context
    groups_by_key: dict[int | None, list[ChapterDraftRecord]] = {}
    order: list[int | None] = []
    for chapter in chapters:
        part_context = get_chapter_part_context(autobiography, chapter.chapter_index)
        key = part_context["part_index"] if part_context else None
        if key not in groups_by_key:
            groups_by_key[key] = []
            order.append(key)
        groups_by_key[key].append(chapter)

    batches: list[list[ChapterDraftRecord]] = []
    for key in order:
        group = groups_by_key[key]
        for start in range(0, len(group), _FINALIZE_BATCH_MAX_CHAPTERS):
            batches.append(group[start : start + _FINALIZE_BATCH_MAX_CHAPTERS])
    return batches


async def _finalize_batch(
    gateways: Gateways,
    *,
    autobiography: AutobiographyRecord,
    batch: list[ChapterDraftRecord],
    style_bible_text: str,
    confirmed: dict | None,
) -> None:
    """배치 하나(대개 Part 하나, 또는 너무 큰 Part의 일부)를 윤문하고 챕터별로
    되써넣는다. 호출 실패(타임아웃 포함)나 마커 파싱 실패 시 예외를 전파하지
    않고 이 배치의 챕터들만 원본을 유지한다 — 배치 하나의 실패가 다른 배치나
    최종본 생성 전체를 막지 않는다(로그만 남김)."""
    from app.autobiography.constants import _FINALIZE_BATCH_MAX_TOKENS
    from app.autobiography.constants import _FINALIZE_BATCH_TIMEOUT_SECONDS
    from app.autobiography.toc import get_chapter_part_context
    from app.autobiography.constants import logger
    manuscript_blocks: list[str] = []
    for chapter in batch:
        part_context = get_chapter_part_context(autobiography, chapter.chapter_index)
        if part_context and part_context["is_part_opening"]:
            manuscript_blocks.append(f"=== PART {part_context['part_index']}: {part_context['part_title']} ===")
        manuscript_blocks.append(
            f"<<<CHAPTER {chapter.chapter_index}>>>\n"
            f"[{chapter.chapter_index}장. {chapter.title}]\n{chapter.content}"
        )
    batch_manuscript = "\n\n".join(manuscript_blocks)

    if confirmed:
        revision_messages = prompts.build_customized_unity_revision_prompt(
            style_bible=style_bible_text,
            full_manuscript=batch_manuscript,
            tone_key=confirmed["tone"],
            concept_key=confirmed["concept"],
        )
    else:
        revision_messages = prompts.build_unity_revision_prompt(
            style_bible=style_bible_text, full_manuscript=batch_manuscript
        )

    chapter_indexes = [chapter.chapter_index for chapter in batch]
    try:
        response = await llm_router.chat_completion(
            revision_messages,
            reasoning_effort="high",
            max_tokens=_FINALIZE_BATCH_MAX_TOKENS,
            timeout=_FINALIZE_BATCH_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning(
            "통일성 윤문 배치 호출 실패(챕터 %s) — 이 배치는 윤문 전 본문을 그대로 유지한다.",
            chapter_indexes,
            exc_info=True,
        )
        return

    revised = response.choices[0].message.content or ""
    if not revised.strip():
        return

    revised_by_index = _split_revised_manuscript_by_chapter(revised, chapter_indexes)
    if revised_by_index is None:
        logger.warning(
            "통일성 윤문 배치 응답의 챕터 마커가 어긋나(챕터 %s) — 이 배치는 "
            "윤문 전 본문을 그대로 유지한다.",
            chapter_indexes,
        )
        return

    for chapter in batch:
        await gateways.chapters.update_content(chapter.id, revised_by_index[chapter.chapter_index])


async def finalize_manuscript(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    """
    Phase 4 통일성 윤문 패스: 전 챕터 생성 후 인접 챕터 경계부와 스타일 바이블을
    함께 검토하는 리비전을 수행한다. 사실 관계·순서는 변경하지 않는다.

    책 전체를 한 번의 호출에 넣지 않고 Part 단위(너무 큰 Part는 더 쪼갬 —
    _group_chapters_for_finalize)로 나눠 여러 번 호출한다(2026-07-19) — 이전에는
    챕터 19개(53,692자)를 한 번에 보내다 API 타임아웃(90초)으로 실패하는 사고가
    실사용 중 재현됐다. 배치로 나누면 호출당 입출력이 훨씬 작아지고, 배치별
    타임아웃도 넉넉히 늘려(_FINALIZE_BATCH_TIMEOUT_SECONDS) 이중으로 방어한다.
    대가는 서로 다른 배치(대개 Part 경계)에 걸친 문체 다듬기는 하지 못한다는
    것인데, Part 경계는 원래도 매끄러운 이음매가 아니라 국면 전환으로 다뤄지는
    지점이라 실질적 손실은 작다. 배치 하나가 실패해도(_finalize_batch 참조)
    그 배치만 원본을 유지하고 나머지는 정상 반영되는 부분 성공을 허용한다.

    윤문 결과는 final_content에만 저장되는 게 아니라 챕터별로 파싱해
    chapter.content에도 되써넣는다(2026-07-18) — PDF 조판이 이 값을 직접
    읽으므로 웹 열람과 실물 책 텍스트가 항상 일치한다.
    """
    from app.autobiography.customization import _get_confirmed_customization
    from app.autobiography.repository import get_autobiography_by_id
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    if not chapters or any(chapter.content is None for chapter in chapters):
        raise ValueError("모든 챕터의 집필(write_chapter)이 끝난 뒤에 최종 윤문을 수행할 수 있습니다.")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    # 커스터마이징이 확정돼 있으면 말투·컨셉 일관성을 윤문에도 반영한다.
    confirmed = _get_confirmed_customization(autobiography)

    batches = _group_chapters_for_finalize(autobiography, chapters)
    for batch in batches:
        await _finalize_batch(
            gateways,
            autobiography=autobiography,
            batch=batch,
            style_bible_text=style_bible_text,
            confirmed=confirmed,
        )

    for chapter in chapters:
        await gateways.chapters.mark_finalized(chapter.id)

    # 배치별로 되써넣어진 최신 챕터 내용을 다시 읽어 final_content를 조립한다
    # (배치 실패로 원본이 유지된 챕터도 그대로 섞여 들어간다 — 부분 성공).
    refreshed_chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    final_content = _join_chapters_into_final_content(refreshed_chapters)

    # AutobiographyStatus.PUBLISHED는 지금까지 enum 값만 정의돼 있고 실제로는
    # 어디서도 설정되지 않던 죽은 값이었다(2026-07-12 발견) — 최종 윤문(이 함수)이
    # 끝나 열람 가능한 완성본이 나오는 시점이 "최종 출판 완료"의 자연스러운 기준이다
    # (PDF 조판·POD는 그 이후의 별도 내보내기 단계).
    autobiography = await gateways.autobiographies.update(
        autobiography_id, final_content=final_content, status=AutobiographyStatus.PUBLISHED
    )
    await gateways.users.update(autobiography.user_id, current_stage=UserStage.PUBLISHED)
    await gateways.commit()
    return autobiography
