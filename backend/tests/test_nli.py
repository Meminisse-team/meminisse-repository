"""
로컬 NLI(자연어 추론) 기반 검증 회귀 테스트.

app/clients/nli.py는 Solar/Embeddings와 달리 "외부 API"가 아니라 이 서버 프로세스
안에서 도는 실제 모델이므로(기획안 6절: "NLI는 로컬 추론"), 여기서는 모킹하지 않고
진짜 모델로 검증한다 — 왜곡 탐지가 실제로 한국어 문장에서 동작하는지가 이 기능의
핵심이기 때문이다(app/services/event_extraction_service.py의 _passes_distortion_check가
이 모듈에 의존한다. 챕터 근거검증은 예전에 이 모듈을 썼으나 Solar LLM 판정 +
groundedness-check API 이중 게이트로 교체되어 더는 의존하지 않는다, 2026-07-17).

첫 실행 시 모델 가중치(수백MB)를 내려받으므로 캐시가 없는 환경에서는 느릴 수 있다 —
tests/test_autobiography_phase34_pipeline.py처럼 파이프라인 배선만 검증하는 테스트는
이 모듈을 모킹해 이 비용을 지지 않는다.
"""

from __future__ import annotations

import pytest

from app.clients import nli
from app.services.autobiography_service import _normalize_place, _resolve_age_to_year
from app.services.event_extraction_service import _passes_distortion_check


def test_split_sentences_splits_on_sentence_boundaries() -> None:
    text = "스무 살 때 혼자 부산에 내려갔다. 거기서 국밥집 아주머니를 만났다."
    sentences = nli.split_sentences(text)
    assert len(sentences) == 2
    assert sentences[0].startswith("스무 살")
    assert sentences[1].startswith("거기서")


@pytest.mark.asyncio
async def test_classify_entailment_batch_recognizes_korean_entailment_and_contradiction() -> None:
    # 프로덕션이 쓰는 유일한 판정 경로는 배치 버전이다(단건 classify_entailment는
    # 호출부가 없어 제거됨, 2026-07-18) — 함의/모순 판정을 한 배치로 함께 검증한다.
    premise = "스무 살 때 혼자 배낭 하나 메고 부산으로 내려갔다. 국밥집 아주머니가 밥을 챙겨주셨다. 나는 부산에서 태어나 부산에서 자랐다."
    results = await nli.classify_entailment_batch(
        premise=premise,
        hypotheses=[
            "나는 부산에서 국밥집 아주머니에게 밥을 얻어먹었다.",
            "나는 서울에서 태어났다.",
        ],
    )
    assert results[0]["entailment"] > 0.7
    assert results[1]["contradiction"] > 0.5


@pytest.mark.asyncio
async def test_distortion_check_passes_for_faithful_reassembly() -> None:
    original_turns = [
        {"role": "assistant", "content": "그때 이야기를 좀 더 해주시겠어요?"},
        {"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요. 국밥집 아주머니가 밥을 챙겨주셨죠."},
    ]
    reassembled_prose = "스무 살 때 혼자 부산으로 내려갔다. 국밥집 아주머니가 밥을 챙겨주셨다."
    assert await _passes_distortion_check(
        original_turns=original_turns, reassembled_prose=reassembled_prose
    )


@pytest.mark.asyncio
async def test_distortion_check_fails_for_fabricated_content() -> None:
    original_turns = [
        {"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요. 국밥집 아주머니가 밥을 챙겨주셨죠."},
    ]
    # 원문에 없는 완전히 다른 사건을 지어낸 "재조립본" — 왜곡 탐지가 걸러야 한다.
    reassembled_prose = "나는 서른 살에 결혼해서 서울에서 신혼집을 차렸다."
    assert not await _passes_distortion_check(
        original_turns=original_turns, reassembled_prose=reassembled_prose
    )


def test_normalize_place_handles_administrative_suffix_variants() -> None:
    assert _normalize_place("부산광역시에서") == "부산"
    assert _normalize_place("서울특별시로") == "서울"
    assert _normalize_place("부산") == "부산"


def test_resolve_age_to_year_handles_digit_and_native_korean_forms() -> None:
    assert _resolve_age_to_year("25세", birth_year=1950) == "1975"
    assert _resolve_age_to_year("스물다섯 살 때", birth_year=1950) == "1975"
    assert _resolve_age_to_year("마흔한 살", birth_year=1950) == "1991"
    assert _resolve_age_to_year("아무 나이 표현 없음", birth_year=1950) is None
    assert _resolve_age_to_year("스물다섯 살", birth_year=None) is None
