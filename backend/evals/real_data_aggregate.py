"""
evals/real_data_comparison.py를 유명인 여러 명에 대해 반복 실행하면 각자
evals/results/real_*/real_<이메일 로컬파트>.json 파일이 남는다. 이 스크립트는
그 파일들을 전부 모아 evals/baseline_ablation_comparison._aggregate/_paired_wilcoxon을
재사용해 full 대비 각 조건(with_followup 포함, 2026-07-18 추가)의 대응표본
Wilcoxon 검정을 실행한다 — 합성 페르소나 쪽(evals/baseline_ablation_comparison.py)
과 통계 로직을 공유하므로 결과 형식도 동일하다. 조건 목록은
evals.real_data_comparison._CONDITIONS를 그대로 가져와 두 스크립트가 어긋나지
않게 한다.

일부 유명인만 --file을 넘겨 with_followup까지 처리하고 나머지는 4개 조건만
처리했어도 문제없다 — _paired_wilcoxon이 값이 없는 쌍(None)은 자동으로
제외하고 짝지어진 쌍만으로 검정하므로, with_followup 표본 수만 자연히 더
적게 잡힌다.

실행 (backend/ 디렉토리에서, 유명인 데이터를 evals/real_data_comparison.py로
전부 처리한 뒤):
    ..\\venv\\Scripts\\python -m evals.real_data_aggregate evals/results/real_*/real_*.json

와일드카드가 셸에서 자동 확장되지 않으면(PowerShell은 보통 확장됨, cmd.exe는
안 됨) evals/results/ 아래를 통째로 스캔하는 기본 동작을 대신 쓴다(인자 생략).
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path
from typing import Any

from evals.baseline_ablation_comparison import _aggregate
from evals.real_data_comparison import _CONDITIONS

_RESULTS_DIR = Path(__file__).parent / "results"


def _discover_report_files(patterns: list[str] | None) -> list[Path]:
    if patterns:
        paths = [Path(p) for pattern in patterns for p in glob.glob(pattern)]
    else:
        paths = sorted(_RESULTS_DIR.glob("real_*/real_*.json"))
    return paths


def main(patterns: list[str] | None = None) -> None:
    paths = _discover_report_files(patterns)
    if not paths:
        print("[경고] evals/results/real_*/real_*.json 형식의 결과 파일을 찾지 못했습니다.")
        return

    reports: dict[str, dict[str, Any]] = {}
    for path in paths:
        persona_id = path.stem  # "real_billgates" 등
        reports[persona_id] = json.loads(path.read_text(encoding="utf-8"))

    print(f"[경고] 표본 n={len(reports)} (기획안 목표 N=30) — n이 이보다 작으면 p-value는 스모크 테스트로만 읽을 것.")

    comparison = _aggregate(reports, condition_names=_CONDITIONS)

    out_path = _RESULTS_DIR / "real_data_aggregate_report.json"
    out_path.write_text(
        json.dumps(
            {"persona_count": len(reports), "conditions": _CONDITIONS, "comparison_vs_full": comparison},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n=== full 대비 조건 비교 (실제 유명인 데이터, n={len(reports)}명) ===")
    for metric_name, per_condition in comparison.items():
        print(f"  [{metric_name}]")
        for condition, result in per_condition.items():
            if result["p_value"] is None:
                print(f"    vs {condition:16s} n={result['n']}  {result['note']}")
            else:
                print(
                    f"    vs {condition:16s} n={result['n']}  full={result['full_mean']:.2f} "
                    f"{condition}={result['other_mean']:.2f}  diff={result['mean_diff']:+.2f}  "
                    f"p={result['p_value']:.3f}"
                )
    print(f"\n상세 결과: {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
