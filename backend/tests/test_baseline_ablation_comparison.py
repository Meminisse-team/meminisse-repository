"""evals/baseline_ablation_comparison.py의 순수 통계 집계 로직(Wilcoxon 래핑) 회귀
테스트. 조건별 원고 생성(evals/baseline_and_ablations.py)과 판정 LLM 호출은 실제
Solar API를 쓰므로 evals/README.md에 문서화된 스크립트 실행으로 검증한다."""

from __future__ import annotations

from evals.baseline_ablation_comparison import _aggregate, _paired_wilcoxon


def test_paired_wilcoxon_returns_none_when_fewer_than_two_pairs() -> None:
    result = _paired_wilcoxon([0.9], [0.5])
    assert result["n"] == 1
    assert result["p_value"] is None
    assert "2건 미만" in result["note"]


def test_paired_wilcoxon_returns_none_when_all_pairs_tied() -> None:
    result = _paired_wilcoxon([0.8, 0.8, 0.8], [0.8, 0.8, 0.8])
    assert result["p_value"] is None
    assert result["mean_diff"] == 0.0


def test_paired_wilcoxon_ignores_pairs_with_missing_scores() -> None:
    # 3번째 페르소나는 full 조건 채점이 실패(None)해 쌍에서 제외돼야 한다.
    full_scores = [0.9, 0.8, None]
    other_scores = [0.5, 0.4, 0.6]
    result = _paired_wilcoxon(full_scores, other_scores)
    assert result["n"] == 2


def test_paired_wilcoxon_computes_statistic_for_valid_pairs() -> None:
    full_scores = [0.9, 0.9, 0.9, 0.9, 0.9, 0.1]
    other_scores = [0.5, 0.4, 0.5, 0.4, 0.5, 0.9]
    result = _paired_wilcoxon(full_scores, other_scores)
    assert result["n"] == 6
    assert result["p_value"] is not None
    assert result["full_mean"] == sum(full_scores) / 6
    assert result["other_mean"] == sum(other_scores) / 6


def test_aggregate_compares_every_non_full_condition_against_full() -> None:
    reports = {
        "p01": {
            "full": {"narrative_coherence": 0.9, "information_preservation": {"precision": 0.9, "recall_curve": {"5": {"recall": 0.8}}}},
            "baseline": {"narrative_coherence": 0.5, "information_preservation": {"precision": 0.6, "recall_curve": {"5": {"recall": 0.5}}}},
        },
        "p02": {
            "full": {"narrative_coherence": 0.85, "information_preservation": {"precision": 0.95, "recall_curve": {"5": {"recall": 0.9}}}},
            "baseline": {"narrative_coherence": 0.4, "information_preservation": {"precision": 0.5, "recall_curve": {"5": {"recall": 0.4}}}},
        },
    }
    comparison = _aggregate(reports, condition_names=["full", "baseline"])
    assert set(comparison.keys()) == {"narrative_coherence", "info_recall_top5", "info_precision"}
    assert "baseline" in comparison["narrative_coherence"]
    assert "full" not in comparison["narrative_coherence"]  # full은 자기 자신과 비교하지 않음
    assert comparison["narrative_coherence"]["baseline"]["n"] == 2
