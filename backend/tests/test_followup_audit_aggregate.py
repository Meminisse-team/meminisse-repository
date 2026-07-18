"""evals/followup_audit_aggregate.py의 순수 집계 로직 회귀 테스트."""

from __future__ import annotations

from evals.followup_audit_aggregate import aggregate


def test_aggregate_sums_counts_across_personas() -> None:
    reports = {
        "followup_audit_a": {
            "name": "인물 A",
            "summary": {
                "total": 2,
                "counts": {"필수슬롯형_꼬리질문": 1, "발동없음": 1},
                "followup_trigger_rate": 0.5,
                "by_type_rate": {},
            },
        },
        "followup_audit_b": {
            "name": "인물 B",
            "summary": {
                "total": 2,
                "counts": {"필수슬롯형_꼬리질문": 2},
                "followup_trigger_rate": 1.0,
                "by_type_rate": {},
            },
        },
    }
    summary = aggregate(reports)
    assert summary["persona_count"] == 2
    assert summary["total_answers"] == 4
    assert summary["overall_counts"]["필수슬롯형_꼬리질문"] == 3
    assert summary["overall_counts"]["발동없음"] == 1
    assert summary["overall_followup_trigger_rate"] == 3 / 4
    assert summary["per_person"]["followup_audit_a"]["name"] == "인물 A"


def test_aggregate_handles_no_reports() -> None:
    summary = aggregate({})
    assert summary["persona_count"] == 0
    assert summary["total_answers"] == 0
    assert summary["overall_followup_trigger_rate"] is None
    assert summary["overall_by_type_rate"] == {}
