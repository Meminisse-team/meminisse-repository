"""evals/followup_trigger_audit.py의 순수 집계 로직 회귀 테스트. classify_answer
자체(실제 Solar 게이팅 호출)는 evals/README.md에 문서화된 스크립트 실행으로
검증한다."""

from __future__ import annotations

from evals.followup_trigger_audit import (
    CATEGORY_CONTEXTUAL,
    CATEGORY_LENGTH,
    CATEGORY_NONE,
    CATEGORY_SLOT,
    summarize,
)


def test_summarize_counts_each_category() -> None:
    results = [
        {"category": CATEGORY_SLOT},
        {"category": CATEGORY_SLOT},
        {"category": CATEGORY_LENGTH},
        {"category": CATEGORY_NONE},
    ]
    summary = summarize(results)
    assert summary["total"] == 4
    assert summary["counts"][CATEGORY_SLOT] == 2
    assert summary["counts"][CATEGORY_LENGTH] == 1
    assert summary["counts"][CATEGORY_NONE] == 1


def test_summarize_computes_overall_followup_trigger_rate() -> None:
    results = [
        {"category": CATEGORY_SLOT},
        {"category": CATEGORY_LENGTH},
        {"category": CATEGORY_CONTEXTUAL},
        {"category": CATEGORY_NONE},
    ]
    summary = summarize(results)
    # 4건 중 3건(슬롯/분량/맥락)이 꼬리질문 발동 — NONE만 미발동.
    assert summary["followup_trigger_rate"] == 3 / 4


def test_summarize_by_type_rate_excludes_non_followup_categories() -> None:
    results = [{"category": CATEGORY_SLOT}, {"category": CATEGORY_NONE}]
    summary = summarize(results)
    assert summary["by_type_rate"][CATEGORY_SLOT] == 0.5
    assert CATEGORY_NONE not in summary["by_type_rate"]


def test_summarize_handles_empty_results() -> None:
    summary = summarize([])
    assert summary["total"] == 0
    assert summary["followup_trigger_rate"] is None
    assert summary["by_type_rate"] == {}
