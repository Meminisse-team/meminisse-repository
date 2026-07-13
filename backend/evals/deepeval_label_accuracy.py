"""
evals/README.md 2절 "DeepEval 라벨추출 정확도".

evals/results/<타임스탬프>/<persona_id>.json의 ground_truth(정답 슬롯)와
extracted_events(실제 추출된 Event 레코드들)를 슬롯 단위로 비교한다. 문자열 완전
일치가 아니라 의미적으로 같은 내용인지(패러프레이즈 허용) 판정해야 하므로,
evals/solar_judge_model.py의 SolarJudgeModel(Solar를 판정 LLM으로 쓰는
DeepEvalBaseLLM 구현체)에게 구조화 출력으로 판정을 받는다.

GEval(자유 형식 criteria 채점)이 아니라 SolarJudgeModel을 직접 쓰는 이유: 이건
"이 산문이 얼마나 일관적인가" 같은 열린 채점이 아니라 "정답 슬롯 값이 추출 결과
어딘가에 의미상 존재하는가"라는 사실 판정에 가까워서, GEval의 루브릭 프레임보다
스키마 기반 직접 판정이 더 적합하고 저렴하다. 같은 SolarJudgeModel을 G-Eval
스크립트(deepeval_narrative_coherence.py)와 공유해 "Solar를 judge로 통일" 결정
(README 2절)을 일관되게 지킨다.

한 세션 = 정답 사건 하나(GroundTruthEvent, evals/personas.py)지만 실제 추출은 여러
개의 세부 Event로 쪼개질 수 있다(예: p01_kim_soonja는 6개). 그래서 슬롯 하나당
"이 페르소나의 추출된 이벤트 전체에서 이 슬롯 값을 찾을 수 있는가"로 recall을,
"추출된 값들이 정답 맥락과 모순 없이 부합하는가"로 precision을 낸다 — 값 하나하나의
부분 정밀도가 아니라 슬롯 단위의 이진 판정으로 단순화했다(스키마를 평평하게 유지해
Upstage Structured Outputs 제약을 피하기 위한 의도적 스코프 축소, 아래 SlotJudgement
참조).

실행:
    cd backend
    ../venv/Scripts/python -m evals.deepeval_label_accuracy
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from evals.solar_judge_model import SolarJudgeModel

_RESULTS_DIR = Path(__file__).parent / "results"
_STAGE_TIMEOUT_SECONDS = 60

_SLOT_LABELS: dict[str, str] = {
    "place": "장소",
    "time": "시기",
    "emotion": "감정",
    "values": "가치관",
    "companion": "동행",
    "gratitude": "감사",
    "regret": "후회",
    "turning_point": "전환점",
    "pride": "자부심",
    "belief": "신념",
    "message": "메시지",
}

# ground_truth 필드명 -> 추출된 Event 레코드(dict)에서 대응 값을 뽑는 함수.
# evals/personas.py GroundTruthEvent 필드 주석의 매핑을 그대로 따른다.
_SLOT_EXTRACTORS: dict[str, Callable[[dict], str | None]] = {
    "place": lambda e: e.get("place"),
    "time": lambda e: e.get("occurred_at_label"),
    "emotion": lambda e: e.get("emotion_tag"),
    "values": lambda e: (e.get("labels") or {}).get("values_reflected"),
    "companion": lambda e: e.get("people"),
    "gratitude": lambda e: (e.get("labels") or {}).get("gratitude"),
    "regret": lambda e: (e.get("labels") or {}).get("regret"),
    "turning_point": lambda e: (e.get("labels") or {}).get("turning_point"),
    "pride": lambda e: (e.get("labels") or {}).get("pride"),
    "belief": lambda e: (e.get("labels") or {}).get("belief"),
    "message": lambda e: (e.get("labels") or {}).get("message"),
}


class SlotJudgement(BaseModel):
    ground_truth_captured: bool
    all_extracted_values_grounded: bool
    reasoning: str


def _build_slot_judge_prompt(*, slot_label: str, ground_truth_value: str, extracted_values: list[str]) -> str:
    candidates = "\n".join(f'- "{v}"' for v in extracted_values) or "- (추출된 값 없음)"
    return f"""아래는 인터뷰 사건 추출 파이프라인의 결과를 검증하는 작업입니다.

[정답] {slot_label}: "{ground_truth_value}"

[추출된 값들] (같은 세션이 여러 사건으로 쪼개지면서 나온 값들입니다):
{candidates}

두 가지를 판단하세요:
1. ground_truth_captured: 위 정답의 의미가 추출된 값들 중 하나 이상에(표현이 달라도,
   패러프레이즈여도) 실질적으로 담겨 있으면 true, 전혀 없으면 false.
2. all_extracted_values_grounded: 추출된 값들이 전부 정답과 모순되지 않고 정답 맥락에서
   자연스럽게 나올 수 있는 내용이면 true. 정답에 없는 내용을 지어냈거나 명백히 다른
   내용이 하나라도 섞여 있으면 false. 추출된 값이 아예 없으면(위에서 "추출된 값 없음")
   판단 대상이 없으므로 true로 둔다(recall 미달과는 별개 문제).

reasoning에 판단 근거를 한두 문장으로 적으세요."""


async def _judge_slot(
    judge: SolarJudgeModel, *, slot_label: str, ground_truth_value: str, extracted_values: list[str]
) -> SlotJudgement:
    prompt = _build_slot_judge_prompt(
        slot_label=slot_label, ground_truth_value=ground_truth_value, extracted_values=extracted_values
    )
    return await asyncio.wait_for(
        judge.a_generate(prompt, schema=SlotJudgement), timeout=_STAGE_TIMEOUT_SECONDS
    )


async def evaluate_persona(judge: SolarJudgeModel, persona_result: dict[str, Any]) -> dict[str, Any]:
    ground_truth = persona_result["ground_truth"]
    extracted_events = persona_result["extracted_events"]
    session_prose = persona_result.get("session_prose", "")

    slot_reports: dict[str, dict[str, Any]] = {}

    # "event"(핵심 사건 서술)는 개별 슬롯이 아니라 재조립된 산문 전체와 비교한다 —
    # 정답이 원래 하나의 사건 요약이고, 추출 쪽은 여러 세부 이벤트로 쪼개져 있어
    # 개별 이벤트 하나와 1:1로 비교할 대상이 없기 때문이다.
    judgement = await _judge_slot(
        judge,
        slot_label="핵심 사건 내용",
        ground_truth_value=ground_truth["event"],
        extracted_values=[session_prose] if session_prose else [],
    )
    slot_reports["event"] = {
        "ground_truth": ground_truth["event"],
        "extracted_values": [session_prose] if session_prose else [],
        **judgement.model_dump(),
    }

    for slot, label in _SLOT_LABELS.items():
        ground_truth_value = ground_truth.get(slot)
        if ground_truth_value is None:
            continue  # 이 페르소나에게 해당 없는 슬롯(선택 슬롯) — 정답 자체가 없으므로 채점 대상 아님.

        extracted_values = [
            value for event in extracted_events if (value := _SLOT_EXTRACTORS[slot](event))
        ]
        judgement = await _judge_slot(
            judge, slot_label=label, ground_truth_value=ground_truth_value, extracted_values=extracted_values
        )
        slot_reports[slot] = {
            "ground_truth": ground_truth_value,
            "extracted_values": extracted_values,
            **judgement.model_dump(),
        }

    return slot_reports


def _aggregate(all_persona_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    per_slot: dict[str, dict[str, int]] = {}
    for persona_id, slot_reports in all_persona_reports.items():
        for slot, report in slot_reports.items():
            bucket = per_slot.setdefault(slot, {"recall_hits": 0, "recall_total": 0, "precision_hits": 0, "precision_total": 0})
            bucket["recall_total"] += 1
            if report["ground_truth_captured"]:
                bucket["recall_hits"] += 1
            if report["extracted_values"]:
                bucket["precision_total"] += 1
                if report["all_extracted_values_grounded"]:
                    bucket["precision_hits"] += 1

    summary = {}
    for slot, bucket in per_slot.items():
        recall = bucket["recall_hits"] / bucket["recall_total"] if bucket["recall_total"] else None
        precision = bucket["precision_hits"] / bucket["precision_total"] if bucket["precision_total"] else None
        summary[slot] = {"recall": recall, "precision": precision, **bucket}
    return summary


def _latest_results_dir() -> Path:
    candidates = [d for d in _RESULTS_DIR.iterdir() if d.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"{_RESULTS_DIR}에 결과 디렉터리가 없습니다 — 먼저 run_benchmark.py를 실행하세요.")
    return sorted(candidates, key=lambda d: d.stat().st_mtime)[-1]


async def main(results_dir: Path | None = None) -> None:
    results_dir = results_dir or _latest_results_dir()
    persona_files = sorted(results_dir.glob("p*.json"))
    if not persona_files:
        print(f"[경고] {results_dir}에 페르소나 결과 파일이 없습니다.")
        return

    judge = SolarJudgeModel()
    all_reports: dict[str, dict[str, Any]] = {}
    for path in persona_files:
        persona_result = json.loads(path.read_text(encoding="utf-8"))
        persona_id = persona_result["persona_id"]
        print(f"[평가 중] {persona_id} ({path.name})")
        try:
            all_reports[persona_id] = await evaluate_persona(judge, persona_result)
        except asyncio.TimeoutError:
            print(f"[실패] {persona_id}: 판정 LLM 호출이 {_STAGE_TIMEOUT_SECONDS}초를 초과했습니다.")

    summary = _aggregate(all_reports)

    output = {
        "results_dir": str(results_dir),
        "persona_count": len(all_reports),
        "per_slot_summary": summary,
        "per_persona_detail": all_reports,
    }
    out_path = results_dir / "label_accuracy_report.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 슬롯별 precision/recall (n={len(all_reports)}명) ===")
    for slot, stats in sorted(summary.items()):
        label = _SLOT_LABELS.get(slot, "핵심 사건 내용" if slot == "event" else slot)
        recall = f"{stats['recall']:.2f}" if stats["recall"] is not None else "N/A"
        precision = f"{stats['precision']:.2f}" if stats["precision"] is not None else "N/A"
        print(
            f"  {label:8s} recall={recall} ({stats['recall_hits']}/{stats['recall_total']})  "
            f"precision={precision} ({stats['precision_hits']}/{stats['precision_total']})"
        )
    print(f"\n상세 결과: {out_path}")


if __name__ == "__main__":
    results_dir_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(results_dir_arg))
