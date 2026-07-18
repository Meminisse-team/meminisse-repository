"""evals/information_preservation.py의 순수 로직(정규화·생존율 계산) 회귀 테스트.
Solar API 호출(extract_keyword_pool, compute_precision)은 evals/README.md에
문서화된 스크립트 실행으로 검증한다."""

from __future__ import annotations

from evals.information_preservation import (
    _normalize,
    compute_recall_curve,
    keyword_survives,
    raw_input_text_from_persona_result,
)


def test_normalize_strips_common_josa_suffixes() -> None:
    assert _normalize("부산에서") == _normalize("부산")
    assert _normalize("어머니는") == _normalize("어머니")
    assert _normalize("1963년에") == _normalize("1963년")


def test_keyword_survives_matches_after_normalization() -> None:
    assert keyword_survives("부산", "나는 부산에서 태어났다.") is True
    assert keyword_survives("어머니", "그해 겨울 어머니는 바느질을 하셨다.") is True
    assert keyword_survives("서울", "나는 부산에서 태어났다.") is False


def test_keyword_survives_handles_empty_keyword() -> None:
    assert keyword_survives("", "아무 내용") is False


def test_compute_recall_curve_reports_per_cutoff_survival() -> None:
    keywords = ["부산", "1963년", "겨울", "어머니", "셋방"]
    final_content = "나는 1963년 겨울 부산의 작은 셋방에서 태어났다."
    curve = compute_recall_curve(keywords, final_content, cutoffs=(3, 5))

    assert curve["3"]["total"] == 3
    assert curve["3"]["survived"] == 3
    assert curve["3"]["recall"] == 1.0

    assert curve["5"]["total"] == 5
    assert curve["5"]["survived"] == 4  # "어머니"는 이 최종 산문에 없음
    assert curve["5"]["recall"] == 0.8


def test_compute_recall_curve_flags_missing_keywords() -> None:
    keywords = ["부산", "서울", "산파"]
    final_content = "나는 부산에서 태어났다."
    curve = compute_recall_curve(keywords, final_content, cutoffs=(3,))
    assert curve["3"]["survived"] == 1
    assert curve["3"]["recall"] == 1 / 3
    assert set(curve["3"]["missing_keywords"]) == {"서울", "산파"}


def test_compute_recall_curve_handles_empty_keyword_pool() -> None:
    curve = compute_recall_curve([], "아무 내용", cutoffs=(5,))
    assert curve["5"] == {"recall": None, "survived": 0, "total": 0}


def test_raw_input_text_from_persona_result_keeps_only_user_turns() -> None:
    persona_result = {
        "transcript": [
            {"role": "user", "content": "저는 부산에서 태어났어요."},
            {"role": "assistant", "content": "그때가 언제였나요?"},
            {"role": "user", "content": "1963년 겨울이었어요."},
        ]
    }
    text = raw_input_text_from_persona_result(persona_result)
    assert "부산에서 태어났어요" in text
    assert "1963년 겨울이었어요" in text
    assert "그때가 언제였나요" not in text
