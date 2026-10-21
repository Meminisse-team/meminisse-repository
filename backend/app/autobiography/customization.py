"""Customization domain operations."""
from __future__ import annotations



import itertools




import uuid



from app.agents import prompts



from app.clients import llm_router

from app.data.question_bank import QUESTION_BANK_BY_SEQUENCE

from app.gateways.dto import AutobiographyRecord

from app.gateways.factory import Gateways




def _get_confirmed_customization(autobiography: AutobiographyRecord) -> dict | None:
    """확정된 커스터마이징 설정을 반환. 없으면 None."""
    style_bible = autobiography.style_bible or {}
    customization = style_bible.get("customization", {})
    return customization.get("confirmed") if customization.get("confirmed") else None


async def save_customization_selection(
    gateways: Gateways,
    autobiography_id: uuid.UUID,
    *,
    tones: list[str],
    structures: list[str],
    concepts: list[str],
) -> AutobiographyRecord:
    """사용자가 각 카테고리에서 2개씩 선택한 결과를 style_bible.customization에 저장한다."""
    from app.autobiography.text import _now_iso
    from app.autobiography.repository import get_autobiography_by_id
    # 유효성 검증
    for tone in tones:
        if tone not in prompts.TONE_OPTIONS:
            raise ValueError(f"유효하지 않은 말투 키: {tone}")
    for structure in structures:
        if structure not in prompts.STRUCTURE_OPTIONS:
            raise ValueError(f"유효하지 않은 구성 키: {structure}")
    for concept in concepts:
        if concept not in prompts.CONCEPT_OPTIONS:
            raise ValueError(f"유효하지 않은 컨셉 키: {concept}")
    if not (1 <= len(tones) <= 2):
        raise ValueError(f"말투는 1~2개를 선택해야 합니다 (현재 {len(tones)}개)")
    if not (1 <= len(structures) <= 2):
        raise ValueError(f"구성은 1~2개를 선택해야 합니다 (현재 {len(structures)}개)")
    if not (1 <= len(concepts) <= 2):
        raise ValueError(f"컨셉은 1~2개를 선택해야 합니다 (현재 {len(concepts)}개)")

    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = dict(autobiography.style_bible or {})
    style_bible["customization"] = {
        "selected_at": _now_iso(),
        "tones": tones,
        "structures": structures,
        "concepts": concepts,
        "confirmed": None,
        "previews": None,
    }
    autobiography = await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()
    return autobiography


async def _recommend_customization_from_tags(
    gateways: Gateways, autobiography: AutobiographyRecord
) -> dict[str, list[str]]:
    """태그 기반 즉석 힌트(Phase 3 이전에도 동작) — 이 유저가 실제로 답변을 남긴
    고정 질문들의 suggested_tags(app/data/question_bank.py)를 모아 말투·구성·컨셉
    각 카테고리별로 어울리는 옵션 키를 빈도순으로 추천한다. 고정 질문 100개는
    모든 유저가 동일한 큐를 거치므로, 전부 답한 유저는 실제 이야기 내용과
    무관하게 전원 같은 조합으로 수렴한다는 한계가 있다 — 그래서 Phase 3가 끝나
    콘텐츠 기반 추천(_generate_content_based_customization_recommendation)이
    준비되면 get_customization_recommendations는 그쪽을 우선한다."""
    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    session_ids = {event.session_id for event in events if event.session_id is not None}
    if not session_ids:
        return {"tones": [], "structures": [], "concepts": []}

    sessions = await gateways.sessions.list_by_user(autobiography.user_id)
    question_ids = {
        session.question_id
        for session in sessions
        if session.id in session_ids and session.question_id is not None
    }

    suggested_tags: list[str] = []
    for question_id in question_ids:
        question = await gateways.questions.get_by_id(question_id)
        if question is None:
            continue
        entry = QUESTION_BANK_BY_SEQUENCE.get(question.sequence_order)
        if entry is not None:
            suggested_tags.extend(entry["suggested_tags"])

    ranked = prompts.recommend_customization_keys(suggested_tags)
    # save_customization_selection이 카테고리당 1~2개를 요구하므로 상위 2개만 추천한다.
    return {
        "tones": ranked["tone"][:2],
        "structures": ranked["structure"][:2],
        "concepts": ranked["concept"][:2],
    }


async def _generate_content_based_customization_recommendation(
    gateways: Gateways, user_id: uuid.UUID, style_bible_text: str
) -> dict | None:
    """Phase 3(consolidate_autobiography)에서 스타일 바이블 생성 직후 한 번 호출된다.
    태그 기반 추천(_recommend_customization_from_tags)과 달리 "어떤 질문에
    답했는가"가 아니라 실제로 화자가 쓴 문체·소재·정서(스타일 바이블 + 중요도 순
    사건 요약)를 LLM에 보여주고 직접 판단하게 한다 — 그래서 같은 100문항에 답했어도
    사람마다 다른 추천이 나올 수 있다. 이벤트가 없으면(사건 추출 전) None을 반환해
    호출부가 태그 기반으로 폴백하게 한다."""
    events = await gateways.events.list_unmerged_verified(user_id)
    if not style_bible_text or not events:
        return None

    event_summaries = "\n".join(
        f"- [중요도 {event.importance_score}] {event.one_line_summary} "
        f"(시기: {event.occurred_at_label or '미상'}, 감정: {event.emotion_tag or '미상'})"
        for event in events[:15]
    )
    result = await llm_router.structured_completion(
        prompts.build_customization_recommendation_prompt(
            style_bible=style_bible_text, event_summaries=event_summaries
        ),
        schema_name="customization_recommendation",
        json_schema=prompts.CUSTOMIZATION_RECOMMENDATION_SCHEMA,
        reasoning_effort="medium",
    )
    return {
        "tones": result.get("tones", [])[:2],
        "structures": result.get("structures", [])[:2],
        "concepts": result.get("concepts", [])[:2],
        "reasoning": result.get("reasoning", ""),
    }


async def get_customization_recommendations(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> dict:
    """말투·구성·컨셉 추천을 반환한다(하이브리드). Phase 3가 끝나 콘텐츠 기반
    추천(style_bible.recommended_customization)이 이미 있으면 그것을 그대로
    쓰고("content_based"), 아직 없으면(Phase 3 이전, 또는 이벤트가 없어 콘텐츠
    기반 추천 자체가 생성되지 않은 경우) 태그 기반 즉석 힌트로 대체한다
    ("tag_based"). 어느 쪽이든 참고용일 뿐 강제가 아니며, save_customization_
    selection 단계에서 사용자는 자유롭게 다른 조합을 선택할 수 있다."""
    from app.autobiography.repository import get_autobiography_by_id
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)

    content_based = (autobiography.style_bible or {}).get("recommended_customization")
    if content_based:
        return {
            "tones": content_based["tones"],
            "structures": content_based["structures"],
            "concepts": content_based["concepts"],
            "source": "content_based",
            "reasoning": content_based.get("reasoning"),
        }

    tag_based = await _recommend_customization_from_tags(gateways, autobiography)
    return {**tag_based, "source": "tag_based", "reasoning": None}


async def generate_sample_previews(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> AutobiographyRecord:
    """
    사용자가 선택한 말투 2 × 구성 2 × 컨셉 2 = 8개 조합에 대해 각각
    맛보기 텍스트(200~400자)를 생성해 style_bible.customization.previews에 저장한다.

    8개를 전부 만든 뒤 한 번에 저장하지 않는다 — 조합별 메타데이터(tone/structure/
    concept/각 *_name)는 LLM 호출 없이 즉시 알 수 있으므로, 먼저 8개 자리표시자
    (preview_text=None, is_generating=True)를 커밋해 프론트가 즉시 8칸을 그릴 수
    있게 하고, 이후 조합이 하나 완성될 때마다 그 자리만 채워 매번 커밋한다
    (story_service.py의 세션 placeholder → 실제 카드 교체와 동일한 사상) — 그래야
    사용자가 8개를 한꺼번에 기다리지 않고 완성되는 대로 하나씩 볼 수 있다.
    """
    from app.autobiography.text import _now_iso
    from app.autobiography.repository import get_autobiography_by_id
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = dict(autobiography.style_bible or {})
    customization = style_bible.get("customization")
    if not customization:
        raise ValueError("먼저 커스터마이징 선택(save_customization_selection)을 완료해야 합니다.")

    tones = customization["tones"]
    structures = customization["structures"]
    concepts = customization["concepts"]
    style_bible_text = style_bible.get("content", "")

    # 이벤트 요약 구성 (상위 이벤트 10개 활용)
    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    event_summaries = "\n".join(
        f"- {event.one_line_summary} (시기: {event.occurred_at_label or '미상'})"
        for event in events[:10]
    )

    combos = list(itertools.product(tones, structures, concepts))
    previews = [
        {
            "tone": tone_key,
            "structure": structure_key,
            "concept": concept_key,
            "tone_name": prompts.TONE_OPTIONS[tone_key]["name"],
            "structure_name": prompts.STRUCTURE_OPTIONS[structure_key]["name"],
            "concept_name": prompts.CONCEPT_OPTIONS[concept_key]["name"],
            "preview_text": None,
            "is_generating": True,
        }
        for tone_key, structure_key, concept_key in combos
    ]
    customization["previews"] = previews
    style_bible["customization"] = customization
    await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()

    for index, (tone_key, structure_key, concept_key) in enumerate(combos):
        messages = prompts.build_sample_preview_prompt(
            tone_key=tone_key,
            structure_key=structure_key,
            concept_key=concept_key,
            style_bible=style_bible_text,
            event_summaries=event_summaries,
        )
        result = await llm_router.structured_completion(
            messages,
            schema_name="sample_preview",
            json_schema=prompts.SAMPLE_PREVIEW_SCHEMA,
            reasoning_effort="medium",
        )
        previews[index]["preview_text"] = result.get("preview_text", "")
        previews[index]["is_generating"] = False
        customization["previews"] = previews
        style_bible["customization"] = customization
        await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
        await gateways.commit()

    customization["previews_generated_at"] = _now_iso()
    style_bible["customization"] = customization
    autobiography = await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()
    return autobiography


async def get_sample_previews(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> list[dict] | None:
    """생성된 샘플 미리보기를 반환. 아직 생성 전이면 None."""
    from app.autobiography.repository import get_autobiography_by_id
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = autobiography.style_bible or {}
    customization = style_bible.get("customization", {})
    return customization.get("previews")


async def confirm_customization(
    gateways: Gateways,
    autobiography_id: uuid.UUID,
    *,
    tone: str,
    structure: str,
    concept: str,
) -> AutobiographyRecord:
    """8개 샘플 중 사용자가 선택한 최종 조합을 확정한다."""
    from app.autobiography.text import _now_iso
    from app.autobiography.repository import get_autobiography_by_id
    if tone not in prompts.TONE_OPTIONS:
        raise ValueError(f"유효하지 않은 말투 키: {tone}")
    if structure not in prompts.STRUCTURE_OPTIONS:
        raise ValueError(f"유효하지 않은 구성 키: {structure}")
    if concept not in prompts.CONCEPT_OPTIONS:
        raise ValueError(f"유효하지 않은 컨셉 키: {concept}")

    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = dict(autobiography.style_bible or {})
    customization = style_bible.get("customization", {})

    customization["confirmed"] = {
        "confirmed_at": _now_iso(),
        "tone": tone,
        "structure": structure,
        "concept": concept,
    }
    style_bible["customization"] = customization
    autobiography = await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()
    return autobiography
