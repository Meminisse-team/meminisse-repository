"""
기획안 6절 "비교 실험 설계(베이스라인 및 어블레이션)" — 실행·통계 검정 스크립트.

evals/run_benchmark.py가 만든 페르소나 결과(evals/results/<타임스탬프>/<persona_id>.json)
를 입력으로, evals/baseline_and_ablations.py의 5개 조건(full/baseline/no_dynamic_toc/
no_event_split/no_followup)을 전부 돌려 각 조건의 최종 원고를 얻은 뒤, 공통 지표
(evals/deepeval_narrative_coherence.py와 같은 G-Eval 서사일관성 +
evals/information_preservation.py의 정보보존율·사실정합률)로 채점하고, full 대비
각 조건의 차이를 대응표본(같은 페르소나가 두 조건 모두에 등장) Wilcoxon
부호순위검정으로 비교한다(기획안: "표본 규모는... 검정력 분석으로 산정(N=30)...
분포 가정이 없는 Wilcoxon 부호순위검정으로 유의성을 평가").

**표본 규모 경고 — 반드시 읽을 것**: 기획안이 요구하는 검정력 있는 표본은 N=30이다.
evals/personas.py는 아직 5명뿐이고(evals/README.md 1절), 그중 세션 후처리까지
완주하는 페르소나는 환경 불안정성 때문에 그보다 더 적을 수 있다. 이 스크립트가
계산하는 p-value는 표본이 5 미만일 때 통계적으로 거의 무의미하다 — scipy가
n<10 정도에서는 정확 분포로 계산해 값 자체는 나오지만, 신뢰할 수 있는 유의성
판단이 아니라 "파이프라인이 실제로 도는지, 방향성이 있는지"를 보는 스모크 테스트로
읽어야 한다. 30명 규모로 늘린 뒤 재실행해야 기획안이 말하는 "검정"이 완성된다.

실행:
    cd backend
    ../venv/Scripts/python -m evals.baseline_ablation_comparison [결과디렉터리] [--conditions=full,baseline,...] [--personas=p01,p02]

결과: evals/results/<타임스탬프>/baseline_ablation_report.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams
from scipy import stats

from evals import baseline_and_ablations, information_preservation
from evals.deepeval_narrative_coherence import _NARRATIVE_COHERENCE_CRITERIA
from evals.solar_judge_model import SolarJudgeModel

_RESULTS_DIR = Path(__file__).parent / "results"
_ALL_CONDITIONS = list(baseline_and_ablations.CONDITIONS.keys())

# evals/run_benchmark.py가 실측한 것과 같은 문제(개발 환경에서 Solar/NLI 호출이
# 간헐적으로 몇 분씩 응답 없이 멈춤, evals/README.md 1절)가 이 스크립트의 조건별
# Phase 3/4 실행(다단계 Solar 호출)에서도 재현될 수 있다. 타임아웃 없이 그대로
# 두면 조건 하나가 멈췄을 때 전체 스크립트가 무기한 정지한다 — run_benchmark.py의
# _stage와 동일한 방어를 조건 단위로 적용해, 하나가 멈춰도 그 조건만 실패 처리하고
# 나머지 조건·페르소나는 계속 진행되게 한다.
_CONDITION_TIMEOUT_SECONDS = 240


async def _score_coherence(judge: SolarJudgeModel, manuscript: dict[str, Any]) -> float | None:
    final_content = manuscript.get("final_content")
    if not final_content:
        return None
    metric = GEval(
        name="Narrative Coherence",
        criteria=_NARRATIVE_COHERENCE_CRITERIA,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=0.5,
    )
    test_case = LLMTestCase(
        input=manuscript.get("book_synopsis") or manuscript.get("title") or "",
        actual_output=final_content,
    )
    await metric.a_measure(test_case, _show_indicator=False)
    return metric.score


async def evaluate_persona_all_conditions(
    judge: SolarJudgeModel, persona_result: dict[str, Any], *, condition_names: list[str]
) -> dict[str, Any]:
    raw_input_text = information_preservation.raw_input_text_from_persona_result(persona_result)
    per_condition: dict[str, Any] = {}
    for name in condition_names:
        runner = baseline_and_ablations.CONDITIONS[name]
        print(f"    [조건] {name} 생성 중...", file=sys.stderr)
        try:
            manuscript = await asyncio.wait_for(runner(persona_result), timeout=_CONDITION_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 — 조건 하나 실패(타임아웃 포함)해도 나머지는 계속
            print(f"    [실패] {name}: {exc!r}", file=sys.stderr)
            per_condition[name] = {"error": repr(exc)}
            continue

        final_content = manuscript.get("final_content") or ""
        try:
            coherence = await asyncio.wait_for(_score_coherence(judge, manuscript), timeout=_CONDITION_TIMEOUT_SECONDS)
            info = await asyncio.wait_for(
                information_preservation.evaluate_manuscript(
                    raw_input_text=raw_input_text, final_content=final_content
                ),
                timeout=_CONDITION_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — 채점 실패해도 원고 자체는 생성됐으니 기록은 남긴다
            print(f"    [채점 실패] {name}: {exc!r}", file=sys.stderr)
            per_condition[name] = {
                "title": manuscript.get("title"),
                "final_content_length": len(final_content),
                "error": f"scoring failed: {exc!r}",
            }
            continue
        per_condition[name] = {
            "title": manuscript.get("title"),
            "final_content_length": len(final_content),
            "narrative_coherence": coherence,
            "information_preservation": info,
        }
    return per_condition


def _paired_wilcoxon(full_scores: list[float | None], other_scores: list[float | None]) -> dict[str, Any]:
    """full 조건과 대조 조건의 같은 페르소나끼리 짝지은 점수를 비교한다. scipy의
    wilcoxon은 (1) 쌍이 너무 적거나 (2) 모든 차이가 0이면 예외를 던지므로, 둘 다
    "검정 불가"로 안전하게 처리한다 — 30명 미만에서는 이 상태가 흔할 것이다."""
    pairs = [(f, o) for f, o in zip(full_scores, other_scores) if f is not None and o is not None]
    if len(pairs) < 2:
        return {"n": len(pairs), "statistic": None, "p_value": None, "note": "쌍이 2건 미만이라 검정 불가"}

    f_vals = [p[0] for p in pairs]
    o_vals = [p[1] for p in pairs]
    diffs = [f - o for f, o in zip(f_vals, o_vals)]
    if all(d == 0 for d in diffs):
        return {
            "n": len(pairs),
            "statistic": None,
            "p_value": None,
            "note": "모든 쌍이 동점이라 검정 불가",
            "mean_diff": 0.0,
        }
    try:
        statistic, p_value = stats.wilcoxon(f_vals, o_vals)
    except ValueError as exc:
        return {"n": len(pairs), "statistic": None, "p_value": None, "note": f"검정 실패: {exc}"}

    return {
        "n": len(pairs),
        "statistic": float(statistic),
        "p_value": float(p_value),
        "full_mean": sum(f_vals) / len(f_vals),
        "other_mean": sum(o_vals) / len(o_vals),
        "mean_diff": sum(diffs) / len(diffs),
    }


_METRIC_EXTRACTORS: dict[str, Any] = {
    "narrative_coherence": lambda c: c.get("narrative_coherence"),
    "info_recall_top5": lambda c: ((c.get("information_preservation") or {}).get("recall_curve", {}).get("5") or {}).get(
        "recall"
    ),
    "info_precision": lambda c: (c.get("information_preservation") or {}).get("precision"),
}


def _aggregate(all_reports: dict[str, dict[str, Any]], *, condition_names: list[str]) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for metric_name, extractor in _METRIC_EXTRACTORS.items():
        comparison[metric_name] = {}
        full_scores = [extractor(report.get("full", {})) for report in all_reports.values()]
        for condition in condition_names:
            if condition == "full":
                continue
            other_scores = [extractor(report.get(condition, {})) for report in all_reports.values()]
            comparison[metric_name][condition] = _paired_wilcoxon(full_scores, other_scores)
    return comparison


def _latest_results_dir() -> Path:
    candidates = [d for d in _RESULTS_DIR.iterdir() if d.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"{_RESULTS_DIR}에 결과 디렉터리가 없습니다 — 먼저 run_benchmark.py를 실행하세요.")
    return sorted(candidates, key=lambda d: d.stat().st_mtime)[-1]


async def main(
    results_dir: Path | None = None,
    *,
    condition_names: list[str] | None = None,
    persona_ids: list[str] | None = None,
) -> None:
    results_dir = results_dir or _latest_results_dir()
    condition_names = condition_names or _ALL_CONDITIONS
    persona_files = sorted(results_dir.glob("p*.json"))
    if persona_ids:
        persona_files = [p for p in persona_files if p.stem in persona_ids]
    if not persona_files:
        print(f"[경고] {results_dir}에 해당하는 페르소나 결과 파일이 없습니다.")
        return

    print(
        f"[경고] 표본 n={len(persona_files)} (기획안 목표 N=30) — p-value는 스모크 테스트로만 "
        f"읽을 것(모듈 docstring 참조).",
        file=sys.stderr,
    )

    judge = SolarJudgeModel()
    all_reports: dict[str, dict[str, Any]] = {}
    for path in persona_files:
        persona_result = json.loads(path.read_text(encoding="utf-8"))
        persona_id = persona_result["persona_id"]
        print(f"[평가 중] {persona_id} ({path.name})", file=sys.stderr)
        all_reports[persona_id] = await evaluate_persona_all_conditions(
            judge, persona_result, condition_names=condition_names
        )

    comparison = _aggregate(all_reports, condition_names=condition_names)

    output = {
        "results_dir": str(results_dir),
        "persona_count": len(all_reports),
        "conditions": condition_names,
        "sample_size_warning": "N=30 미만 — p-value는 스모크 테스트 참고용",
        "comparison_vs_full": comparison,
        "per_persona_detail": all_reports,
    }
    out_path = results_dir / "baseline_ablation_report.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== full 대비 조건 비교 (n={len(all_reports)}명, N=30 미만 — 스모크 테스트) ===")
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


def _parse_csv_arg(argv: list[str], flag: str) -> list[str] | None:
    for arg in argv:
        if arg.startswith(flag + "="):
            return arg[len(flag) + 1 :].split(",")
    return None


if __name__ == "__main__":
    argv = sys.argv[1:]
    positional = [a for a in argv if not a.startswith("--")]
    results_dir_arg = Path(positional[0]) if positional else None
    conditions_arg = _parse_csv_arg(argv, "--conditions")
    personas_arg = _parse_csv_arg(argv, "--personas")
    asyncio.run(main(results_dir_arg, condition_names=conditions_arg, persona_ids=personas_arg))
