"""
evals/followup_trigger_audit.py(Test B)를 유명인 여러 명에 대해 반복 실행하면
각자 evals/results/followup_audit_<파일명>.json이 남는다. 이 스크립트는 그
파일들을 전부 모아 인물 전체를 합친 발동률과 인물별 비교표를 낸다.

evals/real_data_aggregate.py(조건 간 Wilcoxon 검정, 베이스라인/어블레이션 비교용)
와는 목적이 다르다 — 이쪽은 통계적 유의성 검정이 아니라 "실제 100문항 데이터에서
꼬리질문이 얼마나 자주 필요한가"라는 기술 통계(descriptive statistics)다.

실행 (backend/ 디렉토리에서, 유명인 데이터를 evals/followup_trigger_audit.py로
전부 처리한 뒤):
    ..\\venv\\Scripts\\python -m evals.followup_audit_aggregate
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_RESULTS_DIR = Path(__file__).parent / "results"


def _discover_report_files() -> list[Path]:
    return sorted(_RESULTS_DIR.glob("followup_audit_*.json"))


def aggregate(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total_counts: dict[str, int] = {}
    total_answers = 0
    per_person: dict[str, Any] = {}

    for persona_id, report in reports.items():
        summary = report["summary"]
        total_answers += summary["total"]
        for category, count in summary["counts"].items():
            total_counts[category] = total_counts.get(category, 0) + count
        per_person[persona_id] = {
            "name": report.get("name", persona_id),
            "total": summary["total"],
            "followup_trigger_rate": summary["followup_trigger_rate"],
        }

    followup_categories = {"필수슬롯형_꼬리질문", "분량부족형_꼬리질문", "맥락기반형_꼬리질문"}
    followup_total = sum(total_counts.get(c, 0) for c in followup_categories)

    return {
        "persona_count": len(reports),
        "total_answers": total_answers,
        "overall_counts": total_counts,
        "overall_followup_trigger_rate": followup_total / total_answers if total_answers else None,
        "overall_by_type_rate": (
            {c: total_counts.get(c, 0) / total_answers for c in followup_categories} if total_answers else {}
        ),
        "per_person": per_person,
    }


def main() -> None:
    paths = _discover_report_files()
    if not paths:
        print("[경고] evals/results/followup_audit_*.json 형식의 결과 파일을 찾지 못했습니다.")
        return

    reports: dict[str, dict[str, Any]] = {}
    for path in paths:
        reports[path.stem] = json.loads(path.read_text(encoding="utf-8"))

    print(f"[정보] 인물 {len(reports)}명분 꼬리질문 발동 감사 결과를 모읍니다.")
    summary = aggregate(reports)

    out_path = _RESULTS_DIR / "followup_audit_aggregate_report.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 전체 꼬리질문 발동 감사 (인물 {summary['persona_count']}명, 답변 {summary['total_answers']}건) ===")
    for category, count in sorted(summary["overall_counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {category:30s} {count:4d}건 ({count / summary['total_answers']:.0%})")
    print(f"\n전체 꼬리질문 발동률: {summary['overall_followup_trigger_rate']:.0%}")

    print("\n=== 인물별 발동률 ===")
    for persona_id, data in sorted(summary["per_person"].items(), key=lambda kv: -(kv[1]["followup_trigger_rate"] or 0)):
        rate = data["followup_trigger_rate"]
        print(f"  {data['name']:20s} {rate:.0%} (n={data['total']})" if rate is not None else f"  {data['name']:20s} N/A")

    print(f"\n상세 결과: {out_path}")


if __name__ == "__main__":
    main()
