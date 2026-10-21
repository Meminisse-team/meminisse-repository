"""Verification domain operations."""
from __future__ import annotations


import asyncio








from app.agents import prompts


from app.clients import groundedness as groundedness_client

from app.clients import llm_router


from app.gateways.dto import EventRecord





def _normalize_place(text: str) -> str:
    from app.autobiography.constants import _PLACE_ALIASES
    from app.autobiography.constants import _PLACE_PARTICLES
    normalized = text.strip()
    for particle in _PLACE_PARTICLES:
        if normalized.endswith(particle) and len(normalized) > len(particle):
            normalized = normalized[: -len(particle)]
            break
    return _PLACE_ALIASES.get(normalized, normalized)


def _resolve_age_to_year(raw_text: str, *, birth_year: int | None) -> str | None:
    """
    '25세'(숫자) 또는 '스물다섯 살'(고유어) 형태의 나이 표현을 화자의 출생년도와
    더해 절대연도로 환산한다. 세는나이/만나이 차이로 ±1년 오차가 있을 수 있다는
    한계가 있다 — 대조 시 완전 일치가 아닌 근사치임을 감안할 것. birth_year를
    모르거나 나이 표현을 못 찾으면 None(호출부가 기존 문자열 대조로 폴백).
    """
    from app.autobiography.constants import _DIGIT_AGE_PATTERN
    from app.autobiography.constants import _KOREAN_AGE_TENS
    from app.autobiography.constants import _KOREAN_AGE_UNITS
    from app.autobiography.constants import _KOREAN_AGE_WORD_PATTERN
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
    from app.autobiography.text import _now_iso
    if not chapter_content.strip():
        return {"checked_at": _now_iso(), "total_facts": 0, "unchecked_facts": 0, "flags": []}

    extraction = await llm_router.structured_completion(
        prompts.build_fact_reextraction_prompt(chapter_content=chapter_content),
        schema_name="fact_reextraction",
        json_schema=prompts.FACT_REEXTRACTION_SCHEMA,
        reasoning_effort="low",
    )
    facts = extraction.get("facts", [])

    expected_places = {_normalize_place(e.place.lower()) for e in source_events if e.place}
    expected_people = {e.people.lower() for e in source_events if e.people}
    expected_time_labels = {e.occurred_at_label.lower() for e in source_events if e.occurred_at_label}
    # 라벨(place/people/occurred_at_label)은 추출 단계의 요약이라 원문에 실재하는
    # 사실도 빠져 있는 경우가 많다 — 라벨 대조에 실패하면 소환된 사건 원문 문단에서
    # 한 번 더 찾아보고, 원문에 그대로 있으면 플래그하지 않는다(2026-07-18: 라벨에
    # 없는 실존 인물이 챕터마다 반복 플래그되던 구조적 오탐의 수정).
    source_prose_lower = " ".join(
        e.prose_paragraph.lower() for e in source_events if e.prose_paragraph
    )

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

        if not matched and raw_text and raw_text in source_prose_lower:
            matched = True

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


async def _run_groundedness_check(
    chapter_content: str,
    *,
    source_events: list[EventRecord],
    attention_paragraphs: list[str] | None = None,
) -> dict:
    """
    근거 검증(Groundedness Check). 챕터 본문이 소환된 사건들로 뒷받침되는지
    Solar LLM 판정으로 확인한다 — 어떤 사건으로도 뒷받침되지 않는 새로운
    사실·사건을 도입한 문장이 있으면 플래그한다.

    _run_factcheck(문자열 대조)는 "있는 사실이 변형됐는가"만 잡아낼 수 있어(precision),
    원문에 아예 없는 창작 진술은 놓친다 — 이 함수가 그 반대쪽(recall)을 담당한다
    (기획안 4절: "팩트체크(정밀 변형 탐지)와 근거 검증(무근거 창작 탐지)이 각각
    정밀도와 재현율을 분담하는 상보적 2층 구조").

    원래는 로컬 NLI(mDeBERTa) entailment로 문장 단위 대조를 했는데, 감각적
    묘사·내적 성찰 같은 정당한 정교화까지 거의 전부 플래그되는 도구 부적합 문제와
    챕터 하나에 20분 넘게 걸리는 속도 문제가 겹쳐 Solar LLM 판정(단일
    structured_completion)으로 교체했다(2026-07-17). 이때 "애매하면 무조건 통과"
    기준을 썼더니 이번엔 반대로 명백한 날조까지 통과하는 recall 붕괴가 왔다 —
    지금은 비대칭 기준(새 인물/사건/날짜·장소·결과는 애매해도 플래그, 감각 묘사·
    내적 독백은 통과) + reasoning_effort="medium"으로 판정하고, 플래그된 문장은
    2차 게이트(clients/groundedness.py — 원래 Upstage 전용 groundedness-check
    모델이었으나 폐기가 확인돼 solar-mini 이분 판정으로 대체됨, 2026-07-18)로
    한 번 더 확인해 "grounded"로 확정되면 철회한다(판정자 오탐 제거 — 완료 토큰
    한 단어짜리 병렬 호출이라 지연·비용이 거의 없다). 오탐이 남더라도 수리 단계가
    근거 기반으로 다시 쓸 뿐이라 환각이 늘지는 않는다.

    attention_paragraphs: 집필 근거 태그([En]) 없이 작성된 문단들 — 판정자가
    특히 집중해서 검토하도록 프롬프트에 지목한다.
    """
    from app.autobiography.writing import _source_events_text
    from app.autobiography.constants import logger
    if not chapter_content.strip() or not source_events:
        return {
            "checked": False,
            "flags": [],
            "note": "본문 또는 소환된 이벤트가 없어 검증을 생략함",
            "source_event_count": len(source_events),
        }

    source_events_text = _source_events_text(source_events)
    result = await llm_router.structured_completion(
        prompts.build_groundedness_judge_prompt(
            chapter_content=chapter_content,
            source_events_text=source_events_text,
            attention_paragraphs=attention_paragraphs,
        ),
        schema_name="groundedness_judge",
        json_schema=prompts.GROUNDEDNESS_JUDGE_SCHEMA,
        reasoning_effort="medium",
    )
    flags = [
        {"sentence": flag["sentence"], "reason": flag["reason"]} for flag in result.get("flags", [])
    ]

    dismissed_count = 0
    if flags:
        # 2차 게이트: 전용 groundedness-check 모델이 "grounded"라고 확정한 문장만
        # 철회한다(notGrounded/notSure/규약 밖 응답/호출 실패는 전부 플래그 유지 —
        # 검증 실패가 검증 통과로 둔갑하면 안 되므로 보수적으로 처리).
        async def _confirm(flag: dict) -> dict | None:
            try:
                verdict = await groundedness_client.check(
                    context=source_events_text, answer=flag["sentence"]
                )
            except Exception as exc:
                # 보수적 폴백(플래그 유지)은 유지하되 반드시 로그를 남긴다 —
                # 전용 groundedness-check 모델이 폐기된 뒤에도 이 예외가 조용히
                # 삼켜져 2차 게이트가 무력화된 채 몇 주간 아무도 몰랐던 전례가
                # 있다(2026-07-18 발견, clients/groundedness.py 참조).
                logger.warning("groundedness 2차 게이트 호출 실패(플래그 유지): %s", exc)
                return flag
            return None if verdict == groundedness_client.GROUNDED else flag

        confirmed = await asyncio.gather(*(_confirm(flag) for flag in flags))
        dismissed_count = sum(1 for item in confirmed if item is None)
        flags = [item for item in confirmed if item is not None]

    return {
        "checked": True,
        "flags": flags,
        "dismissed_by_groundedness_api": dismissed_count,
        "source_event_count": len(source_events),
    }
