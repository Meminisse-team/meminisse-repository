"""
Phase 4 근거검증 2차 게이트(app/clients/groundedness.py) 모델 비교 검증.

배경: 2차 게이트가 원래 쓰던 Upstage 전용 groundedness-check 모델이 폐기되어
solar-mini 이분 판정으로 대체됐다(2026-07-18, groundedness.py 참조). 이 교체가
"판정 품질이 그대로인지"는 검증된 적이 없었다 — 기존 회귀 테스트
(tests/test_chapter_groundedness_check.py)는 groundedness.check 자체를 모두
mock하므로 오케스트레이션 로직(호출 횟수·플래그 유지/철회)만 검증하고, 실제
solar-mini의 판정 정확도는 다루지 않는다.

이 스크립트는 "정당한 문학적 정교화"와 "날조"를 구분해야 하는 20쌍의 골든셋을
만들어, 현재 기본값(solar-mini)과 대안(solar-pro3, "무료로 쓸 수 있으니 이걸
쓰는 게 낫지 않냐"는 제안)을 같은 데이터로 나란히 실측 비교한다.

측정 방식은 3분류 정확도가 아니라 프로덕션 행동 기준의 이진 판정이다 —
_run_groundedness_check 호출부는 "GROUNDED와 정확히 일치할 때만 플래그 철회,
그 외(notGrounded/notSure/규약 밖 응답)는 전부 플래그 유지"로 보수적으로
해석하므로(groundedness.py docstring), 실제 위험은 다음 두 방향이 비대칭이다:
  - false_grounded(위험한 방향): 정답이 날조인데 "grounded"라 답해 플래그가
    철회됨 — 진짜 환각이 최종 원고까지 살아남는다.
  - over_flagged(안전한 방향): 정답이 정당한 정교화인데 "grounded"가 아니라고
    답해 플래그가 유지됨 — 나중 단계(외과적 수리)가 불필요하게 다시 쓰지만
    환각으로 이어지지는 않는다.
두 모델 다 두 지표로 비교해야 "정확도가 비슷해 보여도 위험한 방향의 실수가 더
많다" 같은 차이를 놓치지 않는다.

실행:
    cd backend
    ../venv/Scripts/python -m evals.groundedness_gate_accuracy

결과: evals/results/<타임스탬프>/groundedness_gate_report.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.clients import groundedness

_RESULTS_DIR = Path(__file__).parent / "results"
_CANDIDATE_MODELS = ["solar-mini", "solar-pro3"]


@dataclass(frozen=True)
class GoldenPair:
    pair_id: str
    context: str  # 소환된 이벤트 문단(들) — 프로덕션과 동일하게 "- 문단" 형태로 조립
    answer: str  # 근거검증 대상 챕터 문장
    expected: str  # "grounded" | "notGrounded" — 사람이 정답으로 판단한 라벨
    note: str  # 왜 이 라벨인지(정교화 종류 / 날조 종류)


# 정당한 정교화(grounded여야 함) 10건 + 날조(notGrounded여야 함) 10건.
# 도메인은 실제 파이프라인 데이터 형태(1인칭 시니어 화자 사건 문단 → 3인칭
# 아닌 문학적 챕터 문장)를 그대로 따른다.
GOLDEN_SET: list[GoldenPair] = [
    # ── 정당한 정교화 (expected=grounded) ──────────────────────────────
    GoldenPair(
        "g01_sensory_detail",
        context="- 나는 1963년 겨울 부산 셋방에서 태어났다. 그해 겨울은 유난히 추웠다고 어머니가 늘 말씀하셨다.",
        answer="살을 에는 듯한 그 겨울, 나는 부산의 작은 셋방에서 첫 울음을 터뜨렸다.",
        expected="grounded",
        note="원문의 '유난히 추웠다'를 감각적으로 재서술 — 새 사실 추가 없음",
    ),
    GoldenPair(
        "g02_interior_monologue",
        context="- 대학 합격자 발표가 난 날, 아버지는 아무 말씀 없이 내 어깨만 두드리셨다.",
        answer="말없이 어깨를 두드리시던 아버지의 손길에서, 나는 평생 처음 그분의 자랑스러움을 읽었다.",
        expected="grounded",
        note="원문에 명시된 행동(어깨를 두드림)에 대한 화자의 내적 해석 — 정당한 정서적 정교화",
    ),
    GoldenPair(
        "g03_paraphrase_relative_time",
        context="- 스물다섯 되던 해, 나는 서울로 상경해 방직공장에 취직했다.",
        answer="스물다섯의 나이로 낯선 서울 땅을 밟은 나는 방직공장 노동자가 되었다.",
        expected="grounded",
        note="문학적 어휘 변주(상경/노동자)일 뿐 사실 관계는 원문과 동일",
    ),
    GoldenPair(
        "g04_combining_two_facts",
        context="- 남편은 3년간 병상에 누워 있었다.\n- 나는 그 사이 삯바느질로 생계를 꾸렸다.",
        answer="병든 남편을 돌보는 3년 동안, 나는 밤낮으로 바늘을 놀려 가족을 먹였다.",
        expected="grounded",
        note="두 개의 근거 문단을 하나의 자연스러운 문장으로 결합 — 새 사실 없음",
    ),
    GoldenPair(
        "g05_emotion_inference_from_stated_outcome",
        context="- 셋째 아이가 태어난 지 사흘 만에 숨을 거두었다.",
        answer="그 사흘은 내 평생 가장 길고 아픈 시간이었다.",
        expected="grounded",
        note="명시된 비극적 사건에서 합리적으로 따라나오는 감정 서술",
    ),
    GoldenPair(
        "g06_weather_consistent_with_scene",
        context="- 피난길에 오른 것은 1951년 1월, 한겨울이었다.",
        answer="발이 얼어붙는 추위 속에서도 우리는 걸음을 멈출 수 없었다.",
        expected="grounded",
        note="원문에 이미 '한겨울'로 명시된 계절에 부합하는 감각 묘사",
    ),
    GoldenPair(
        "g07_gesture_elaboration",
        context="- 아들이 입대하던 날, 나는 기차역까지 배웅을 나갔다.",
        answer="멀어지는 기차를 향해 손을 흔들면서도, 나는 애써 눈물을 감췄다.",
        expected="grounded",
        note="배웅이라는 명시된 행동에 자연스럽게 수반되는 동작·감정 묘사",
    ),
    GoldenPair(
        "g08_summary_sentence",
        context="- 스무 살에 결혼해 이듬해 첫딸을 낳았다.\n- 남편은 농사를 지었고 나는 집안 살림을 도맡았다.",
        answer="스무 살 어린 신부였던 나는 이듬해 첫딸을 품에 안았고, 농사짓는 남편 곁에서 살림을 꾸려 나갔다.",
        expected="grounded",
        note="근거 문단들의 요약·재배열이며 새로운 사실 유입 없음",
    ),
    GoldenPair(
        "g09_metaphor",
        context="- 광복이 되었을 때 나는 열 살이었고, 온 동네가 태극기를 들고 나와 만세를 불렀다.",
        answer="그날 거리를 가득 메운 만세 소리는 지금도 내 귓가에 생생하다.",
        expected="grounded",
        note="명시된 사건(만세)에 대한 화자의 회고적 감상 — 비유적 표현이지 새 사실이 아님",
    ),
    GoldenPair(
        "g10_cause_stated_explicitly",
        context="- 가세가 기울어 중학교 진학은 꿈도 꾸지 못했다.",
        answer="집안 형편이 어려워지면서, 나는 교복 입은 또래들을 그저 부러운 눈으로 바라볼 수밖에 없었다.",
        expected="grounded",
        note="원문이 명시한 원인(가세가 기울어)을 그대로 잇는 정교화",
    ),
    # ── 날조 (expected=notGrounded) ─────────────────────────────────────
    GoldenPair(
        "n01_new_named_person",
        context="- 나는 1963년 겨울 부산 셋방에서 태어났다.",
        answer="산파 김말순 할머니가 밤새 나를 받아냈다고 어머니는 늘 말씀하셨다.",
        expected="notGrounded",
        note="원문에 없는 새 인물(산파 이름)과 새 일화를 창작",
    ),
    GoldenPair(
        "n02_new_specific_date",
        context="- 대학 합격자 발표가 난 날, 아버지는 아무 말씀 없이 내 어깨만 두드리셨다.",
        answer="1982년 3월 2일, 합격자 발표가 난 그날 아버지는 내 어깨를 두드리셨다.",
        expected="notGrounded",
        note="원문에 없는 구체적 연월일을 날조 — 원문은 '그날'만 언급",
    ),
    GoldenPair(
        "n03_new_location",
        context="- 남편은 3년간 병상에 누워 있었다.",
        answer="서울대병원 특실에 누운 남편을 매일 찾아가 간호했다.",
        expected="notGrounded",
        note="원문에 없는 구체적 장소(병원명·병실 등급)를 창작",
    ),
    GoldenPair(
        "n04_fabricated_outcome",
        context="- 셋째 아이가 태어난 지 사흘 만에 숨을 거두었다.",
        answer="그 일로 나는 오랫동안 병원 신세를 지며 우울증 진단을 받았다.",
        expected="notGrounded",
        note="원문에 없는 새로운 결과(입원·진단명)를 지어냄",
    ),
    GoldenPair(
        "n05_fabricated_dialogue",
        context="- 아들이 입대하던 날, 나는 기차역까지 배웅을 나갔다.",
        answer="\"어머니, 꼭 살아 돌아오겠습니다\" 아들은 그렇게 말하며 경례를 붙였다.",
        expected="notGrounded",
        note="원문에 없는 직접 인용(대사)을 창작",
    ),
    GoldenPair(
        "n06_wrong_family_relation",
        context="- 스무 살에 결혼해 이듬해 첫딸을 낳았다.",
        answer="시어머니의 구박 속에서 첫딸을 낳았다.",
        expected="notGrounded",
        note="원문에 없는 인물(시어머니)과 관계 갈등을 새로 창작",
    ),
    GoldenPair(
        "n07_new_number",
        context="- 피난길에 오른 것은 1951년 1월, 한겨울이었다.",
        answer="꼬박 열이틀을 걸어서야 겨우 목적지에 도착했다.",
        expected="notGrounded",
        note="원문에 없는 구체적 소요 일수를 창작",
    ),
    GoldenPair(
        "n08_new_event_entirely",
        context="- 광복이 되었을 때 나는 열 살이었고, 온 동네가 태극기를 들고 나와 만세를 불렀다.",
        answer="그날 밤 마을 잔치에서 나는 처음으로 무대에 올라 노래를 불렀다.",
        expected="notGrounded",
        note="원문과 무관한 완전히 새로운 사건(무대 공연)을 창작",
    ),
    GoldenPair(
        "n09_wrong_cause_attribution",
        context="- 가세가 기울어 중학교 진학은 꿈도 꾸지 못했다.",
        answer="아버지의 노름빚 때문에 집안이 기울어 중학교에 갈 수 없었다.",
        expected="notGrounded",
        note="원문에 없는 구체적 원인(노름빚)을 창작해 인물을 부정적으로 묘사",
    ),
    GoldenPair(
        "n10_contradicted_emotion",
        context="- 남편은 3년간 병상에 누워 있었다.\n- 나는 그 사이 삯바느질로 생계를 꾸렸다.",
        answer="힘들었지만 그 시절이 오히려 우리 부부에게는 가장 행복한 신혼 같았다.",
        expected="notGrounded",
        note="원문의 고단한 정황과 정면으로 모순되는 정서(행복한 신혼)를 창작",
    ),
]


async def _judge_one(pair: GoldenPair, *, model: str) -> dict:
    try:
        verdict = await asyncio.wait_for(
            groundedness.check(context=pair.context, answer=pair.answer, model=model),
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 — 실패도 결과에 남겨 집계에서 다뤄야 한다
        verdict = f"ERROR:{exc}"

    predicted_dismiss = verdict == groundedness.GROUNDED
    expected_dismiss = pair.expected == "grounded"
    return {
        "pair_id": pair.pair_id,
        "expected": pair.expected,
        "verdict": verdict,
        "correct": predicted_dismiss == expected_dismiss,
        # 위험한 방향: 날조인데 grounded로 오판(플래그가 잘못 철회됨).
        "false_grounded": expected_dismiss is False and predicted_dismiss is True,
        # 안전한 방향: 정당한 정교화인데 notGrounded/notSure로 과다 플래그.
        "over_flagged": expected_dismiss is True and predicted_dismiss is False,
    }


def _aggregate(results: list[dict]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    false_grounded = [r for r in results if r["false_grounded"]]
    over_flagged = [r for r in results if r["over_flagged"]]
    notGrounded_total = sum(1 for r in results if r["expected"] == "notGrounded")
    grounded_total = sum(1 for r in results if r["expected"] == "grounded")
    return {
        "n": total,
        "accuracy": correct / total if total else None,
        "false_grounded_count": len(false_grounded),
        "false_grounded_rate": (len(false_grounded) / notGrounded_total) if notGrounded_total else None,
        "false_grounded_pair_ids": [r["pair_id"] for r in false_grounded],
        "over_flagged_count": len(over_flagged),
        "over_flagged_rate": (len(over_flagged) / grounded_total) if grounded_total else None,
        "over_flagged_pair_ids": [r["pair_id"] for r in over_flagged],
    }


async def main() -> None:
    run_dir = _RESULTS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {"golden_set_size": len(GOLDEN_SET), "models": {}}
    for model in _CANDIDATE_MODELS:
        print(f"[진행중] model={model} — {len(GOLDEN_SET)}쌍 판정", file=sys.stderr)
        results = await asyncio.gather(*(_judge_one(pair, model=model) for pair in GOLDEN_SET))
        summary = _aggregate(results)
        report["models"][model] = {"summary": summary, "detail": results}
        print(
            f"[완료] {model}: accuracy={summary['accuracy']:.2f}  "
            f"false_grounded={summary['false_grounded_count']}/{sum(1 for p in GOLDEN_SET if p.expected == 'notGrounded')}"
            f"(위험)  over_flagged={summary['over_flagged_count']}/{sum(1 for p in GOLDEN_SET if p.expected == 'grounded')}(안전)",
            file=sys.stderr,
        )

    out_path = run_dir / "groundedness_gate_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 모델 비교 (n={len(GOLDEN_SET)}쌍) ===")
    for model, data in report["models"].items():
        s = data["summary"]
        print(
            f"  {model:12s} accuracy={s['accuracy']:.2f}  "
            f"false_grounded_rate={s['false_grounded_rate']:.2f} (위험: 날조를 grounded로 오판)  "
            f"over_flagged_rate={s['over_flagged_rate']:.2f} (안전: 정교화를 과다 플래그)"
        )
    print(f"\n상세 결과: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
