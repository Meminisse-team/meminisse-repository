"""Consolidation domain operations."""
from __future__ import annotations


import asyncio




import statistics

import uuid


from decimal import Decimal

from app.agents import prompts



from app.clients import llm_router


from app.gateways.dto import AutobiographyRecord, EventImportanceUpdate, EventRecord

from app.gateways.factory import Gateways

from app.models.enums import AutobiographyStatus, LifeMilestoneCategory, UserStage



async def consolidate_autobiography(gateways: Gateways, user_id: uuid.UUID) -> AutobiographyRecord:
    """
    모든 인터뷰 세션 종료 후 호출되는 Phase 3 진입점. 순서가 중요하다 — 병합을 먼저
    끝내야 중복 흡수된 이벤트가 중요도 산정의 반복 언급 신호(mention_count)에 반영된다.
    """
    from app.autobiography.customization import _generate_content_based_customization_recommendation
    from app.autobiography.repository import get_or_create_autobiography
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
    from app.autobiography.constants import EVENT_MERGE_CANDIDATE_LIMIT
    from app.autobiography.constants import EVENT_MERGE_CANDIDATE_MAX_DISTANCE
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
        # 같은 canonical에 대한 후보 판정들은 서로 독립이라 병렬로 돌린다(2026-07-18
        # — 순차 LLM 호출이 병합 단계의 주 병목이었다). canonical 간 순서는
        # merged_ids 의존성(이미 흡수된 이벤트 건너뛰기) 때문에 그대로 순차 유지.
        fresh_candidates = [c for c in candidates if c.id not in merged_ids]
        verdicts = await asyncio.gather(
            *(_judge_same_event(canonical, candidate) for candidate in fresh_candidates)
        )
        for candidate, same_event in zip(fresh_candidates, verdicts):
            if same_event:
                await gateways.events.mark_duplicate(candidate.id, duplicate_of_event_id=canonical.id)
                merged_ids.add(candidate.id)


async def _judge_same_event(event_a: EventRecord, event_b: EventRecord) -> bool:
    messages = prompts.build_event_merge_judge_prompt(
        event_a_summary=f"{event_a.one_line_summary} ({event_a.occurred_at_label or '시기 미상'})",
        event_b_summary=f"{event_b.one_line_summary} ({event_b.occurred_at_label or '시기 미상'})",
    )
    result = await llm_router.structured_completion(
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
    from app.autobiography.constants import MILESTONE_BONUS
    from app.autobiography.constants import MUST_INCLUDE_BONUS
    from app.autobiography.constants import WEIGHT_EMOTION_INTENSITY
    from app.autobiography.constants import WEIGHT_LENGTH_Z
    from app.autobiography.constants import WEIGHT_MENTION_COUNT
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
    from app.autobiography.text import _now_iso
    all_prose = await gateways.sessions.list_session_prose_by_user(user_id)
    if not all_prose:
        return None

    response = await llm_router.chat_completion(
        prompts.build_style_bible_prompt(all_session_prose=all_prose),
        reasoning_effort="medium",
    )
    return {"generated_at": _now_iso(), "content": response.choices[0].message.content or ""}
