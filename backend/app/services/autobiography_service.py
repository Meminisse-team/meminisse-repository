"""
Phase 3(이벤트 병합·중요도 산정·스타일 바이블)과 Phase 4(동적 목차·하향식 집필·
팩트체크·근거검증·제3자 위해성 분류) 오케스트레이션.

Phase 3/4는 무거운 LLM 호출이 여러 번 이어지는 연산이므로(기획안 4절: "세션 종료 후
이벤트 추출, 최종 집필 ... Celery+Redis 독립 워커에서 처리"), 이 모듈의 최상위 진입점
(consolidate_autobiography, write_chapter, finalize_manuscript)은 app/workers/tasks.py의
Celery 태스크를 통해 호출되어야 API 서버 타임아웃을 피할 수 있다. 목차 생성/선택처럼
LLM 호출 1회로 끝나는 가벼운 단계는 API 요청 경로에서 직접 await해도 무방하다.

DB 접근은 전부 app.gateways를 통한다 — 이 파일은 SQLAlchemy를 알지 못한다. 공개
진입점(위 5개 + get_or_create_autobiography 등 router가 직접 부르는 함수)만 각자
gateways.commit()을 한 번 호출한다; 비공개(`_` 접두) 헬퍼는 커밋하지 않는다.
"""

from __future__ import annotations

import itertools
import re
import statistics
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.agents import prompts
from app.clients import embeddings as embeddings_client
from app.clients import nli, solar
from app.data.question_bank import QUESTION_BANK_BY_SEQUENCE
from app.gateways.dto import (
    AutobiographyRecord,
    ChapterDraftCreateData,
    ChapterDraftRecord,
    ChapterDraftWriteResult,
    EventImportanceUpdate,
    EventRecord,
)
from app.gateways.factory import Gateways
from app.models.enums import AutobiographyStatus, DraftStatus, LifeMilestoneCategory, UserStage
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

# 근거 검증(Groundedness Check): 생성 문장의 함의(entailment) 확률이 이 값 미만이면
# "소환된 이벤트 문단에 근거가 없다"고 플래그한다. 실사용 데이터로 캘리브레이션 전까지의
# 잠정값(event_extraction_service._DISTORTION_CONTRADICTION_THRESHOLD와 짝을 이룸).
GROUNDEDNESS_ENTAILMENT_THRESHOLD = 0.5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create_autobiography(gateways: Gateways, user_id: uuid.UUID) -> AutobiographyRecord:
    autobiography = await gateways.autobiographies.get_by_user_id(user_id)
    if autobiography is not None:
        return autobiography
    autobiography = await gateways.autobiographies.create(user_id)
    await gateways.commit()
    return autobiography


async def get_autobiography_by_id(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    autobiography = await gateways.autobiographies.get_by_id(autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {autobiography_id} not found")
    return autobiography


async def list_chapter_drafts(gateways: Gateways, autobiography_id: uuid.UUID) -> list[ChapterDraftRecord]:
    return await gateways.chapters.list_by_autobiography(autobiography_id)


async def get_chapter_draft(gateways: Gateways, chapter_draft_id: uuid.UUID) -> ChapterDraftRecord | None:
    return await gateways.chapters.get(chapter_draft_id)


# --------------------------------------------------------------------------- #
# 자서전 커스터마이징 — 말투·구성·컨셉 선택 / 샘플 미리보기 / 확정             #
# --------------------------------------------------------------------------- #


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
    result = await solar.structured_completion(
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
    """
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

    previews = []
    for tone_key, structure_key, concept_key in itertools.product(tones, structures, concepts):
        messages = prompts.build_sample_preview_prompt(
            tone_key=tone_key,
            structure_key=structure_key,
            concept_key=concept_key,
            style_bible=style_bible_text,
            event_summaries=event_summaries,
        )
        result = await solar.structured_completion(
            messages,
            schema_name="sample_preview",
            json_schema=prompts.SAMPLE_PREVIEW_SCHEMA,
            reasoning_effort="medium",
        )
        previews.append({
            "tone": tone_key,
            "structure": structure_key,
            "concept": concept_key,
            "tone_name": prompts.TONE_OPTIONS[tone_key]["name"],
            "structure_name": prompts.STRUCTURE_OPTIONS[structure_key]["name"],
            "concept_name": prompts.CONCEPT_OPTIONS[concept_key]["name"],
            "preview_text": result.get("preview_text", ""),
        })

    customization["previews"] = previews
    customization["previews_generated_at"] = _now_iso()
    style_bible["customization"] = customization
    autobiography = await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()
    return autobiography


async def get_sample_previews(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> list[dict] | None:
    """생성된 샘플 미리보기를 반환. 아직 생성 전이면 None."""
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


# --------------------------------------------------------------------------- #
# Phase 3: 이벤트 병합 · 중요도 산정 · 스타일 바이블                            #
# --------------------------------------------------------------------------- #

async def consolidate_autobiography(gateways: Gateways, user_id: uuid.UUID) -> AutobiographyRecord:
    """
    모든 인터뷰 세션 종료 후 호출되는 Phase 3 진입점. 순서가 중요하다 — 병합을 먼저
    끝내야 중복 흡수된 이벤트가 중요도 산정의 반복 언급 신호(mention_count)에 반영된다.
    """
    autobiography = await get_or_create_autobiography(gateways, user_id)

    consolidated_content = await _build_consolidated_content(gateways, user_id)
    await _merge_duplicate_events(gateways, user_id)
    await _score_importance(gateways, user_id)
    style_bible = await _generate_style_bible(gateways, user_id)

    if style_bible is not None:
        # 스타일 바이블이 막 만들어진 시점이라야 이 사람의 실제 문체·사건을 근거로
        # 한 콘텐츠 기반 커스터마이징 추천을 만들 재료가 갖춰진다 — 없으면(이벤트
        # 없음) None이 돌아오고, get_customization_recommendations가 태그 기반으로
        # 폴백한다.
        recommendation = await _generate_content_based_customization_recommendation(
            gateways, user_id, style_bible["content"]
        )
        if recommendation is not None:
            style_bible["recommended_customization"] = recommendation

    autobiography = await gateways.autobiographies.update(
        autobiography.id,
        status=AutobiographyStatus.CONSOLIDATED,
        consolidated_content=consolidated_content,
        style_bible=style_bible,
    )
    # User.current_stage: 목차 생성·챕터 조립(Phase 4)이 이제부터 시작되므로
    # "publishing" 단계로 전환한다(어디서도 갱신되지 않던 버그, 2026-07-12 발견 —
    # interview_service.create_session의 동일한 수정 참조).
    await gateways.users.update(user_id, current_stage=UserStage.PUBLISHING)
    await gateways.commit()
    return autobiography


async def _build_consolidated_content(gateways: Gateways, user_id: uuid.UUID) -> str:
    """Autobiography.consolidated_content: 완료된 세션의 산문을 시간순으로 이어붙인
    열람용 원본. LLM 입력으로 재사용하지 않는다(모델 docstring 참조)."""
    prose = await gateways.sessions.list_session_prose_by_user(user_id)
    return "\n\n".join(prose)


async def _merge_duplicate_events(gateways: Gateways, user_id: uuid.UUID) -> None:
    """
    Phase 3 이벤트 병합·정합성 검토(기획안). 임베딩 유사도는 병합 '후보' 탐색에만
    쓰고, 실제 병합 여부는 LLM 쌍별 판정으로 결정한다. 판정이 불확실하면 병합하지
    않는 것이 기본값이다(과병합은 인쇄 후 회복 불가, 과분리는 사용자 확인으로 즉시
    회복 가능하다는 리스크 비대칭 — prompts.EVENT_MERGE_JUDGE_SYSTEM_PROMPT 참조).
    """
    canonical_candidates = await gateways.events.list_mergeable(user_id)
    merged_ids: set[uuid.UUID] = set()

    for canonical in canonical_candidates:
        if canonical.id in merged_ids:
            continue  # 이전 반복에서 이미 다른 이벤트로 흡수됨

        candidates = await gateways.events.find_merge_candidates(
            user_id=user_id,
            exclude_event_id=canonical.id,
            embedding=canonical.embedding,
            max_distance=EVENT_MERGE_CANDIDATE_MAX_DISTANCE,
            limit=EVENT_MERGE_CANDIDATE_LIMIT,
        )
        for candidate in candidates:
            if candidate.id in merged_ids:
                continue
            if await _judge_same_event(canonical, candidate):
                await gateways.events.mark_duplicate(candidate.id, duplicate_of_event_id=canonical.id)
                merged_ids.add(candidate.id)


async def _judge_same_event(event_a: EventRecord, event_b: EventRecord) -> bool:
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


async def _score_importance(gateways: Gateways, user_id: uuid.UUID) -> None:
    """
    Phase 3 객관적 중요도 스코어링. LLM 주관 판단이 아니라 계산 가능한 신호의
    가중합 + 사용자 내 z-score 정규화(발화량 편차 보정)로 산정한다(기획안).
    importance_signals에 산출 근거를 남겨 "왜 이 사건이 목차에 들어갔는가"를
    재현 가능하게 설명한다.
    """
    events = await gateways.events.list_unmerged_verified(user_id)
    if not events:
        return

    lengths = [len(event.prose_paragraph) for event in events]
    mean_length = statistics.mean(lengths)
    stdev_length = statistics.pstdev(lengths)
    mention_counts = await gateways.events.count_mentions([event.id for event in events])

    updates: list[EventImportanceUpdate] = []
    for event in events:
        z_length = (len(event.prose_paragraph) - mean_length) / stdev_length if stdev_length > 0 else 0.0
        mention_count = mention_counts.get(event.id, 0) + 1  # +1: 본인 자신도 1회 언급으로 계산
        milestone = prompts.classify_life_milestone_category(
            f"{event.one_line_summary} {event.prose_paragraph}"
        )

        score = (
            WEIGHT_LENGTH_Z * z_length
            + WEIGHT_EMOTION_INTENSITY * (event.emotion_intensity or 0)
            + WEIGHT_MENTION_COUNT * (mention_count - 1)
            + (MILESTONE_BONUS if milestone else 0.0)
            + (MUST_INCLUDE_BONUS if event.is_must_include else 0.0)
        )
        updates.append(
            EventImportanceUpdate(
                event_id=event.id,
                importance_score=Decimal(str(round(score, 3))),
                importance_signals={
                    "raw_length": len(event.prose_paragraph),
                    "z_length": round(z_length, 3),
                    "emotion_intensity": event.emotion_intensity,
                    "mention_count": mention_count,
                    "life_milestone_category": milestone,
                    "is_must_include": event.is_must_include,
                },
                life_milestone_category=LifeMilestoneCategory(milestone) if milestone else None,
            )
        )

    await gateways.events.bulk_update_importance(updates)


async def _generate_style_bible(gateways: Gateways, user_id: uuid.UUID) -> dict | None:
    all_prose = await gateways.sessions.list_session_prose_by_user(user_id)
    if not all_prose:
        return None

    response = await solar.chat_completion(
        prompts.build_style_bible_prompt(all_session_prose=all_prose),
        reasoning_effort="medium",
    )
    return {"generated_at": _now_iso(), "content": response.choices[0].message.content or ""}


# --------------------------------------------------------------------------- #
# Phase 4: 동적 목차 · 하향식 집필 · 팩트체크 · 근거검증 · 등장인물 스캔        #
# --------------------------------------------------------------------------- #

async def generate_toc_candidates(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)

    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    if not events:
        raise ValueError("목차를 생성하려면 먼저 Phase 3(consolidate_autobiography)이 완료되어야 합니다.")

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

    result_json = await solar.structured_completion(
        toc_messages,
        schema_name="toc_generation",
        json_schema=prompts.TOC_GENERATION_SCHEMA,
        reasoning_effort="medium",
    )
    toc_data = {
        "generated_at": _now_iso(),
        "candidates": result_json["candidates"],
        "selected_candidate_index": None,
    }
    autobiography = await gateways.autobiographies.update(autobiography_id, toc_data=toc_data)
    await gateways.commit()
    return autobiography


async def select_toc_candidate(
    gateways: Gateways, autobiography_id: uuid.UUID, candidate_index: int
) -> AutobiographyRecord:
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    if not autobiography.toc_data or not autobiography.toc_data.get("candidates"):
        raise ValueError("먼저 목차 후보를 생성해야 합니다(generate_toc_candidates).")

    candidates = autobiography.toc_data["candidates"]
    if not (0 <= candidate_index < len(candidates)):
        raise ValueError(f"candidate_index={candidate_index}가 후보 범위를 벗어났습니다(총 {len(candidates)}개).")

    chosen = candidates[candidate_index]
    updated_toc = {**autobiography.toc_data, "selected_candidate_index": candidate_index}

    # 재선택 시 이전 챕터 초안을 대체한다(idempotent).
    await gateways.chapters.replace_all(
        autobiography.id,
        [
            ChapterDraftCreateData(chapter_index=chapter["chapter_index"], title=chapter["title"])
            for chapter in chosen["chapters"]
        ],
    )

    book_synopsis = await _generate_book_synopsis(autobiography, chosen)
    title = await _generate_book_title(autobiography, chosen)

    autobiography = await gateways.autobiographies.update(
        autobiography_id, toc_data=updated_toc, book_synopsis=book_synopsis, title=title
    )
    await gateways.commit()
    return autobiography


def _toc_text(selected_toc: dict) -> str:
    return "\n".join(
        f"{chapter['chapter_index']}. {chapter['title']} ({', '.join(chapter.get('theme_keywords', []))})"
        for chapter in selected_toc["chapters"]
    )


async def _generate_book_synopsis(autobiography: AutobiographyRecord, selected_toc: dict) -> str:
    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    response = await solar.chat_completion(
        prompts.build_book_synopsis_prompt(style_bible=style_bible_text, toc=_toc_text(selected_toc)),
        reasoning_effort="medium",
    )
    return response.choices[0].message.content or ""


async def _generate_book_title(autobiography: AutobiographyRecord, selected_toc: dict) -> str:
    """표지·PDF 조판(app/services/pdf_service.py)에 그대로 노출되는 책 제목.
    toc/select 이전에는 Autobiography.title이 채워질 방법이 아예 없었다 — 목차가
    확정돼야 비로소 책 전체를 관통하는 제목을 지을 컨텍스트(스타일 바이블 + 목차)가
    갖춰지므로, book_synopsis와 같은 시점에 함께 생성한다."""
    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    result = await solar.structured_completion(
        prompts.build_book_title_prompt(style_bible=style_bible_text, toc=_toc_text(selected_toc)),
        schema_name="book_title",
        json_schema=prompts.BOOK_TITLE_SCHEMA,
        reasoning_effort="low",
    )
    return result["title"].strip()


async def _retrieve_events_for_chapter(
    gateways: Gateways, user_id: uuid.UUID, chapter: ChapterDraftRecord
) -> list[EventRecord]:
    """
    하이브리드 검색(의미 검색 + 키워드 정확 매칭). ChapterDraft는 theme_keywords를
    영속화하지 않으므로(이번 작업은 서비스 레이어로 범위를 한정했다 — DB 스키마
    확장은 별도 논의 대상) 챕터 제목을 쿼리로 사용하는 1차 근사치다.

    두 축 모두 EventGateway.search_verified/search_by_keywords를 통하므로 Layer 1
    검증 게이트(verified=True, duplicate_of_event_id IS NULL)가 항상 적용된다.
    """
    query_text = chapter.title or ""
    semantic_events: list[EventRecord] = []
    if query_text:
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


async def _previous_chapter_summary(
    gateways: Gateways, autobiography_id: uuid.UUID, chapter_index: int
) -> str | None:
    if chapter_index <= 1:
        return None
    previous = await gateways.chapters.get_by_index(autobiography_id, chapter_index - 1)
    if previous is None or not previous.content:
        return None
    # 직전 챕터 전문 대신 말미 일부만 전달 — 실제 요약 생성 LLM 호출을 아끼는 근사치.
    return previous.content[-1000:]


def _total_flag_count(factcheck_report: dict, groundedness_report: dict) -> int:
    return len(factcheck_report.get("flags", [])) + len(groundedness_report.get("flags", []))


async def write_chapter(gateways: Gateways, chapter_draft_id: uuid.UUID) -> ChapterDraftRecord:
    """
    Phase 4 하향식 집필의 챕터 단위 실행: [챕터 시놉시스 생성 → 하이브리드 RAG 소환 →
    본문 집필 → 팩트체크 → 근거검증 → 등장인물 스캔]을 한 챕터에 대해 순서대로 수행한다.
    """
    chapter = await gateways.chapters.get(chapter_draft_id)
    if chapter is None:
        raise ValueError(f"ChapterDraft {chapter_draft_id} not found")
    autobiography = await gateways.autobiographies.get_by_id(chapter.autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {chapter.autobiography_id} not found")
    if not autobiography.book_synopsis:
        raise ValueError("먼저 목차를 선택해 책 전체 시놉시스를 생성해야 합니다(select_toc_candidate).")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")

    retrieved_events = await _retrieve_events_for_chapter(gateways, autobiography.user_id, chapter)
    source_event_ids = [event.id for event in retrieved_events]

    chapter_synopsis = await _generate_chapter_synopsis(
        book_synopsis=autobiography.book_synopsis,
        chapter_title=chapter.title or f"{chapter.chapter_index}장",
        event_summaries=[event.one_line_summary for event in retrieved_events],
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
    )

    narrator = await gateways.users.get_by_id(autobiography.user_id)
    birth_year = narrator.birth_year if narrator else None
    factcheck_report = await _run_factcheck(
        content, source_events=retrieved_events, birth_year=birth_year
    )
    groundedness_report = await _run_groundedness_check(content, source_events=retrieved_events)

    if _total_flag_count(factcheck_report, groundedness_report) > 0:
        # 팩트체크/근거검증에 걸리면 한 번 더 같은 자료로 재집필을 시도한다 — 결과가
        # 확정적이지 않은 LLM 생성물이라, 다시 쓰면 지어낸 문장 없이 나올 가능성이
        # 있다(2026-07-16, factcheck_report/groundedness_report가 계산만 되고 아무
        # 데도 노출되지 않던 문제의 해결책 중 하나로 도입 — session_prose 큐잉의
        # 즉시 재시도 패턴과 같은 발상). 재시도 결과가 원래보다 flag가 적을 때만
        # 채택한다 — 생성은 확률적이라 재시도가 오히려 나빠질 수도 있어서, "무조건
        # 재시도 결과 사용"이 아니라 "더 나은 쪽을 채택"으로 안전하게 판단한다.
        retry_content = await _generate_chapter_content(
            style_bible=style_bible_text,
            book_synopsis=autobiography.book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_summary,
            retrieved_event_paragraphs=[event.prose_paragraph for event in retrieved_events],
            tone_key=confirmed["tone"] if confirmed else None,
            concept_key=confirmed["concept"] if confirmed else None,
        )
        retry_factcheck = await _run_factcheck(
            retry_content, source_events=retrieved_events, birth_year=birth_year
        )
        retry_groundedness = await _run_groundedness_check(
            retry_content, source_events=retrieved_events
        )
        if _total_flag_count(retry_factcheck, retry_groundedness) < _total_flag_count(
            factcheck_report, groundedness_report
        ):
            content, factcheck_report, groundedness_report = (
                retry_content,
                retry_factcheck,
                retry_groundedness,
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

    await gateways.commit()
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
    tone_key: str | None = None,
    concept_key: str | None = None,
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
        )
    else:
        messages = prompts.build_chapter_writing_prompt(
            style_bible=style_bible,
            book_synopsis=book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_chapter_summary,
            retrieved_event_paragraphs=retrieved_event_paragraphs,
        )
    response = await solar.chat_completion(messages, reasoning_effort="high")
    return response.choices[0].message.content or ""


# 행정구역 명칭 변이(정식 명칭/구어체) 정규화. 기획안 예시("고향 바닷가"=부산 같은
# 완전한 의미 추론은 범위 밖이지만, 흔한 정식/구어 표기 차이는 결정론적으로 흡수한다.
_PLACE_ALIASES: dict[str, str] = {
    "서울시": "서울", "서울특별시": "서울",
    "부산시": "부산", "부산광역시": "부산",
    "대구시": "대구", "대구광역시": "대구",
    "인천시": "인천", "인천광역시": "인천",
    "광주시": "광주", "광주광역시": "광주",
    "대전시": "대전", "대전광역시": "대전",
    "울산시": "울산", "울산광역시": "울산",
    "세종시": "세종", "세종특별자치시": "세종",
}
# 긴 조사부터 매치해야 "에서부터"가 "에서"로 잘못 잘리는 일이 없다.
_PLACE_PARTICLES = ("에서부터", "으로부터", "에서는", "에서", "으로는", "부터", "까지", "에는", "으로", "에", "로")


def _normalize_place(text: str) -> str:
    normalized = text.strip()
    for particle in _PLACE_PARTICLES:
        if normalized.endswith(particle) and len(normalized) > len(particle):
            normalized = normalized[: -len(particle)]
            break
    return _PLACE_ALIASES.get(normalized, normalized)


# 나이의 고유어(순우리말) 표현 → 숫자. "스물다섯 살"처럼 십 단위+일 단위가 붙어
# 나오는 시니어 구어체 패턴을 커버한다(정확한 형태소 분석기 없이 결정론적으로 처리).
_KOREAN_AGE_TENS: dict[str, int] = {
    "스물": 20, "서른": 30, "마흔": 40, "쉰": 50,
    "예순": 60, "일흔": 70, "여든": 80, "아흔": 90,
}
_KOREAN_AGE_UNITS: dict[str, int] = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
}
_DIGIT_AGE_PATTERN = re.compile(r"(\d{1,3})\s*(?:살|세)")
_KOREAN_AGE_WORD_PATTERN = re.compile(
    "(" + "|".join(sorted(_KOREAN_AGE_TENS, key=len, reverse=True)) + ")"
    "(" + "|".join(sorted(_KOREAN_AGE_UNITS, key=len, reverse=True)) + ")?"
    r"\s*(?:살|세)"
)


def _resolve_age_to_year(raw_text: str, *, birth_year: int | None) -> str | None:
    """
    '25세'(숫자) 또는 '스물다섯 살'(고유어) 형태의 나이 표현을 화자의 출생년도와
    더해 절대연도로 환산한다. 세는나이/만나이 차이로 ±1년 오차가 있을 수 있다는
    한계가 있다 — 대조 시 완전 일치가 아닌 근사치임을 감안할 것. birth_year를
    모르거나 나이 표현을 못 찾으면 None(호출부가 기존 문자열 대조로 폴백).
    """
    if birth_year is None:
        return None
    digit_match = _DIGIT_AGE_PATTERN.search(raw_text)
    if digit_match:
        return str(birth_year + int(digit_match.group(1)))
    word_match = _KOREAN_AGE_WORD_PATTERN.search(raw_text)
    if word_match:
        age = _KOREAN_AGE_TENS[word_match.group(1)] + _KOREAN_AGE_UNITS.get(word_match.group(2) or "", 0)
        return str(birth_year + age)
    return None


async def _run_factcheck(
    chapter_content: str, *, source_events: list[EventRecord], birth_year: int | None = None
) -> dict:
    """
    원문 대조 팩트체크(재추출-정규화-대조). 지명은 정식/구어 표기 변이를 정규화하고,
    나이 표현(숫자·고유어)은 화자 출생년도로 절대연도 환산해 대조한다("스물다섯 되던
    해"=1975년 같은 경우를 커버). 인명 별칭 매핑(가족 호칭·애칭 등)은 본격적인 개체
    연결이 필요해 이번 범위에서는 대소문자 무시 부분 문자열 매칭으로 남겨둔다.
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

    expected_places = {_normalize_place(e.place.lower()) for e in source_events if e.place}
    expected_people = {e.people.lower() for e in source_events if e.people}
    expected_time_labels = {e.occurred_at_label.lower() for e in source_events if e.occurred_at_label}

    flags = []
    unchecked = 0
    for fact in facts:
        fact_type = fact["fact_type"]
        raw_text = fact["raw_text"].strip().lower()

        if fact_type == "quantity":
            # Event 모델에 수량 필드가 없어 대조 기준이 없다 — 오탐 방지를 위해 검증
            # 대상에서 제외하고 unchecked로만 집계한다.
            unchecked += 1
            continue
        if fact_type == "place":
            normalized = _normalize_place(raw_text)
            matched = any(normalized in exp or exp in normalized for exp in expected_places)
        elif fact_type == "year_or_age":
            matched = any(raw_text in exp or exp in raw_text for exp in expected_time_labels)
            if not matched:
                resolved_year = _resolve_age_to_year(fact["raw_text"], birth_year=birth_year)
                if resolved_year:
                    matched = any(resolved_year in exp for exp in expected_time_labels)
        else:  # person
            matched = any(raw_text in exp or exp in raw_text for exp in expected_people)

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


async def _run_groundedness_check(chapter_content: str, *, source_events: list[EventRecord]) -> dict:
    """
    근거 검증(Groundedness Check). 생성된 각 문장을 소환된 이벤트 문단(source_events의
    prose_paragraph 전체)과 짝지어 NLI로 함의(entailment) 여부를 판정한다 — 함의되지
    않는 문장은 원문에 근거 없이 지어낸 진술일 가능성이 있어 플래그한다.

    _run_factcheck(문자열 대조)는 "있는 사실이 변형됐는가"만 잡아낼 수 있어(precision),
    원문에 아예 없는 창작 진술은 놓친다 — 이 함수가 그 반대쪽(recall)을 담당한다
    (기획안 4절: "팩트체크(정밀 변형 탐지)와 근거 검증(무근거 창작 탐지)이 각각
    정밀도와 재현율을 분담하는 상보적 2층 구조").
    """
    if not chapter_content.strip() or not source_events:
        return {
            "checked": False,
            "flags": [],
            "note": "본문 또는 소환된 이벤트가 없어 검증을 생략함",
            "source_event_count": len(source_events),
        }

    combined_sources = "\n".join(event.prose_paragraph for event in source_events)
    sentences = nli.split_sentences(chapter_content)

    flags = []
    for sentence in sentences:
        result = await nli.classify_entailment(premise=combined_sources, hypothesis=sentence)
        if result["entailment"] < GROUNDEDNESS_ENTAILMENT_THRESHOLD:
            flags.append(
                {
                    "sentence": sentence,
                    "entailment_score": round(result["entailment"], 3),
                    "reason": "not_entailed_by_sources",
                }
            )

    return {
        "checked": True,
        "flags": flags,
        "total_sentences": len(sentences),
        "source_event_count": len(source_events),
    }


async def finalize_manuscript(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    """
    Phase 4 통일성 윤문 패스: 전 챕터 생성 후 인접 챕터 경계부와 스타일 바이블을
    함께 검토하는 리비전을 1회 수행한다. 사실 관계·순서는 변경하지 않는다.
    """
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    if not chapters or any(chapter.content is None for chapter in chapters):
        raise ValueError("모든 챕터의 집필(write_chapter)이 끝난 뒤에 최종 윤문을 수행할 수 있습니다.")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    full_manuscript = "\n\n".join(
        f"[{chapter.chapter_index}장. {chapter.title}]\n{chapter.content}" for chapter in chapters
    )

    # 커스터마이징이 확정돼 있으면 말투·컨셉 일관성을 윤문에도 반영한다.
    confirmed = _get_confirmed_customization(autobiography)
    if confirmed:
        revision_messages = prompts.build_customized_unity_revision_prompt(
            style_bible=style_bible_text,
            full_manuscript=full_manuscript,
            tone_key=confirmed["tone"],
            concept_key=confirmed["concept"],
        )
    else:
        revision_messages = prompts.build_unity_revision_prompt(
            style_bible=style_bible_text, full_manuscript=full_manuscript
        )

    response = await solar.chat_completion(revision_messages, reasoning_effort="high")
    final_content = response.choices[0].message.content or full_manuscript

    for chapter in chapters:
        await gateways.chapters.mark_finalized(chapter.id)

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
