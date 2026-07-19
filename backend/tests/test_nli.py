"""
문장 분리 유틸리티(app/clients/nli.py) 회귀 테스트.

이 모듈은 원래 로컬 NLI(mDeBERTa) 모델 클라이언트였다 — 왜곡 탐지·근거 검증에
쓰였으나 둘 다 속도 문제(세션당 190~210초 / 챕터당 20분, 로컬 CPU 추론)로 Solar
LLM 판정으로 교체됐다(근거 검증은 2026-07-17, 왜곡 탐지는 2026-07-19). 지금 남은
건 다른 곳(event_extraction_service._strip_leaked_assistant_sentences)에서 계속
쓰이는 순수 정규식 유틸리티 split_sentences뿐이다 — 왜곡 탐지 자체의 회귀
테스트는 tests/test_distortion_check.py 참조.
"""

from __future__ import annotations

from app.clients import nli
from app.services.autobiography_service import _normalize_place, _resolve_age_to_year


def test_split_sentences_splits_on_sentence_boundaries() -> None:
    text = "스무 살 때 혼자 부산에 내려갔다. 거기서 국밥집 아주머니를 만났다."
    sentences = nli.split_sentences(text)
    assert len(sentences) == 2
    assert sentences[0].startswith("스무 살")
    assert sentences[1].startswith("거기서")


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
