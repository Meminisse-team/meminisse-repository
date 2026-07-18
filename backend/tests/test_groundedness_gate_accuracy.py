"""
evals/groundedness_gate_accuracy.py의 순수 집계 로직 회귀 테스트.

실제 판정(groundedness.check을 통한 Solar API 호출)은 evals/README.md에 문서화된
스크립트 실행으로 검증하지, 여기서는 모킹하지 않는다(비용/속도) — 여기서는
_aggregate의 위험/안전 방향 분리 집계만 검증한다.
"""

from __future__ import annotations

from evals.groundedness_gate_accuracy import GOLDEN_SET, _aggregate


def test_golden_set_has_balanced_grounded_and_not_grounded_pairs() -> None:
    grounded = [p for p in GOLDEN_SET if p.expected == "grounded"]
    not_grounded = [p for p in GOLDEN_SET if p.expected == "notGrounded"]
    assert len(grounded) == len(not_grounded) == 10
    assert len({p.pair_id for p in GOLDEN_SET}) == len(GOLDEN_SET)  # id 중복 없음


def test_aggregate_counts_false_grounded_as_the_dangerous_direction() -> None:
    """날조(expected=notGrounded)인데 grounded로 오판된 경우만 false_grounded로
    집계되어야 한다 — 이게 최종 원고에 환각이 새는 위험한 방향이다."""
    results = [
        {
            "pair_id": "n01",
            "expected": "notGrounded",
            "verdict": "grounded",
            "correct": False,
            "false_grounded": True,
            "over_flagged": False,
        },
        {
            "pair_id": "n02",
            "expected": "notGrounded",
            "verdict": "notGrounded",
            "correct": True,
            "false_grounded": False,
            "over_flagged": False,
        },
        {
            "pair_id": "g01",
            "expected": "grounded",
            "verdict": "grounded",
            "correct": True,
            "false_grounded": False,
            "over_flagged": False,
        },
    ]
    summary = _aggregate(results)
    assert summary["n"] == 3
    assert summary["accuracy"] == 2 / 3
    assert summary["false_grounded_count"] == 1
    assert summary["false_grounded_rate"] == 1 / 2  # notGrounded 전체 2건 중 1건
    assert summary["false_grounded_pair_ids"] == ["n01"]
    assert summary["over_flagged_count"] == 0


def test_aggregate_counts_over_flagged_as_the_safe_direction() -> None:
    """정당한 정교화(expected=grounded)인데 notGrounded/notSure로 과다 플래그된
    경우는 over_flagged로 분리 집계되어야 한다 — 환각으로 이어지진 않지만
    불필요한 재작성 비용을 유발하는 별개의 실패 유형이다."""
    results = [
        {
            "pair_id": "g01",
            "expected": "grounded",
            "verdict": "notSure",
            "correct": False,
            "false_grounded": False,
            "over_flagged": True,
        },
        {
            "pair_id": "g02",
            "expected": "grounded",
            "verdict": "grounded",
            "correct": True,
            "false_grounded": False,
            "over_flagged": False,
        },
    ]
    summary = _aggregate(results)
    assert summary["over_flagged_count"] == 1
    assert summary["over_flagged_rate"] == 1 / 2  # grounded 전체 2건 중 1건
    assert summary["over_flagged_pair_ids"] == ["g01"]
    assert summary["false_grounded_count"] == 0


def test_aggregate_handles_empty_results() -> None:
    summary = _aggregate([])
    assert summary["n"] == 0
    assert summary["accuracy"] is None
    assert summary["false_grounded_rate"] is None
    assert summary["over_flagged_rate"] is None
