"""
증명③(정량 지표 공개) — "NLI 기반 지표로 원본 대비 사실 왜곡·환각 정도를
수치화, 구조적 유사성·문장력도 함께 공개"를 이미 시딩·처리된 실제 인물
데이터(예: billgates@dummy.com) 한 명에 대해 곧바로 계산한다.

증명①(원작 대결)·증명②(벤치마크 재현 대조)와 달리 새 파이프라인이 필요 없다
— 이미 만든 부품을 조합한다:
  - Phase 2 왜곡 탐지 결과(session.distortion_flagged) — **주의: 원래 로컬
    NLI 판정이었으나 2026-07-19 세션당 190~210초로 실측돼 프로덕션에서도
    Solar LLM 판정(solar-mini)으로 교체됐다. 즉 이것도 모델 의견이지 결정론적
    수치가 아니다.**
  - Phase 4 챕터별 팩트체크·근거검증 리포트(chapter.factcheck_report/
    groundedness_report, write_chapter이 실제로 이미 만들어 저장해 둔 것 —
    새로 계산하지 않고 그대로 집계만 한다). 이 둘의 성격이 다르다는 걸
    리포트에서 구분해 보여준다:
      · factcheck_report: LLM은 사실(인명·연도·지명)만 추출하고, 최종 판정은
        정규화된 문자열 대조(기획안 4절 설계) — 이 시스템에서 유일하게
        "모델 의견이 아닌" 결정론적 신호다.
      · groundedness_report: LLM의 판단(solar-pro3 1차 + solar-mini 2차 게이트)
        — 의견 기반.
  - G-Eval 서사일관성(문장력·구조적 자연스러움의 대리 지표) — 이것도 LLM
    판정(judge)이다.
  - 정보보존율 recall 곡선 — 원본에서 키워드를 뽑는 데만 Solar를 쓰고(추출
    대상이 원본이지 우리 시스템의 출력이 아니다), 실제 생존 여부 판정은
    순수 문자열 매칭이라 이쪽은 자기선호 편향 우려가 없다.
  - 결정론적 구조 통계(Part 수, 챕터 수, 챕터 분량 균형)

**"문장 단위 독립 재검증"(compute_precision 재사용) 단계는 뺐다(2026-07-19,
사용자 확인)** — Phase 4 2차 게이트(groundedness.py)와 완전히 같은 모델
(solar-mini)을 다시 부르는 것이라 "독립"이라는 이름값을 못 하고, 자기선호
편향 문제(evals/groundedness_gate_accuracy.py에서 이미 실측된 문제, 챕터
집필·1차 판정도 이미 solar-pro3/solar-mini 계열이라 검증까지 같은 계열로
채우면 그 계열이 놓치는 오류를 구조적으로 못 잡는다)만 반복할 뿐이다.

1명(또는 소수) 처리로 충분하다는 게 핵심이다 — 30명 규모 통계적 유의성이
아니라 "우리 시스템이 이 정도로 검증됐다"를 발표 슬라이드 하나로 보여주는
용도다. 다만 위 구분(결정론적 vs LLM 의견)을 발표에서도 명확히 해야 한다 —
"NLI 기반이라 객관적"이라는 원래 기획 문구는 NLI가 삭제된 지금 더 이상
정확하지 않다.

실행 (backend/ 디렉토리에서, seed_dummy.py + process_seeded_sessions.py로
이미 처리된 인물 대상):
    ..\\venv\\Scripts\\python -m evals.quantitative_report --email "billgates@dummy.com" \\
        --chapter-concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from evals import information_preservation, parallel_chapters
from evals.baseline_ablation_comparison import _score_coherence
from evals.solar_judge_model import SolarJudgeModel
from app.gateways.factory import gateways_context

_RESULTS_DIR = Path(__file__).parent / "results"


async def _get_raw_input_text(gateways, user_id) -> str:
    sessions = await gateways.sessions.list_by_user(user_id)
    chunks: list[str] = []
    for session_summary in sessions:
        session = await gateways.sessions.get_by_id(session_summary.id)
        chunks.extend(log.content for log in session.chat_logs if log.role.value == "user")
    return "\n".join(chunks)


async def _phase2_distortion_stats(gateways, user_id) -> dict:
    sessions = await gateways.sessions.list_by_user(user_id)
    total = len(sessions)
    flagged = sum(1 for s in sessions if s.distortion_flagged)
    return {
        "total_sessions": total,
        "distortion_flagged_sessions": flagged,
        "distortion_flagged_rate": flagged / total if total else None,
    }


async def _phase4_own_verification_stats(gateways, autobiography_id) -> dict:
    """write_chapter이 실제 집필 시점에 이미 만들어 저장해 둔 팩트체크·근거검증
    리포트를 그대로 집계한다 — 새 판정을 돌리지 않는다(모듈 docstring 참조)."""
    chapters = await gateways.chapters.list_by_autobiography(autobiography_id)
    total_factcheck_flags = 0
    total_groundedness_flags = 0
    for chapter in chapters:
        total_factcheck_flags += len((chapter.factcheck_report or {}).get("flags", []))
        total_groundedness_flags += len((chapter.groundedness_report or {}).get("flags", []))
    return {
        "chapter_count": len(chapters),
        "total_factcheck_flags": total_factcheck_flags,
        "total_groundedness_flags": total_groundedness_flags,
        "flags_per_chapter": (
            (total_factcheck_flags + total_groundedness_flags) / len(chapters) if chapters else None
        ),
    }


def _structural_stats(toc_data: dict | None, chapters: list) -> dict:
    selected = None
    if toc_data and toc_data.get("candidates") and toc_data.get("selected_candidate_index") is not None:
        selected = toc_data["candidates"][toc_data["selected_candidate_index"]]

    part_count = len(selected.get("parts") or []) if selected else None
    lengths = [len(c.content) for c in chapters if c.content]
    return {
        "part_count": part_count,
        "chapter_count": len(chapters),
        "chapter_length_mean": statistics.mean(lengths) if lengths else None,
        "chapter_length_stdev": statistics.pstdev(lengths) if len(lengths) > 1 else None,
        "chapter_length_min": min(lengths) if lengths else None,
        "chapter_length_max": max(lengths) if lengths else None,
    }


async def run_report(
    email: str, *, chapter_concurrency: int, regenerate: bool = False, geval_runs: int = 3
) -> dict:
    async with gateways_context() as gateways:
        user = await gateways.users.get_by_email(email)
        if user is None:
            raise ValueError(f"{email}에 해당하는 유저가 없습니다 — seed_dummy.py를 먼저 실행하세요.")
        user_id = user.id
        raw_input_text = await _get_raw_input_text(gateways, user_id)
        phase2_stats = await _phase2_distortion_stats(gateways, user_id)
        existing = [] if regenerate else await gateways.autobiographies.list_finished_by_user(user_id)

    if existing:
        # 이미 완성된 원고가 있으면 재생성하지 않는다 — 토큰·시간 낭비 방지
        # (2026-07-19 사용자 요청: "빌게이츠 자서전은 이미 생성해둔 상태").
        # 여러 버전이 있으면 최신 것(list_finished_by_user가 created_at desc로
        # 정렬해 반환)을 쓴다. --regenerate로 강제 재생성 가능.
        print(f"[1/4] 기존 완성 원고 재사용 (autobiography_id={existing[0].id}, 재생성 생략)", file=sys.stderr)
        autobiography = existing[0]
        final_content = autobiography.final_content or ""
        manuscript = {
            "title": autobiography.title,
            "book_synopsis": autobiography.book_synopsis,
            "final_content": final_content,
        }
    else:
        print("[1/4] 완성 원고 생성 중(챕터 병렬 집필)...", file=sys.stderr)
        manuscript = await parallel_chapters.run_phase34_parallel(user_id, chapter_concurrency=chapter_concurrency)
        final_content = manuscript.get("final_content") or ""
        async with gateways_context() as gateways:
            autobiography = (await gateways.autobiographies.list_finished_by_user(user_id))[0]

    async with gateways_context() as gateways:
        chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
        phase4_own_stats = await _phase4_own_verification_stats(gateways, autobiography.id)
        structural_stats = _structural_stats(autobiography.toc_data, chapters)

    # "문장 단위 독립 재검증" 단계는 뺐다 — Phase 4 2차 게이트(groundedness.py)와
    # 완전히 같은 모델(solar-mini)을 다시 부르는 것이라 자기선호 편향만 반복할
    # 뿐 진짜 독립적인 신호가 아니다(모듈 docstring, 2026-07-19 사용자 확인).

    print("[2/4] 정보보존율(recall) 계산 중...", file=sys.stderr)
    keywords = await information_preservation.extract_keyword_pool(raw_input_text, top_k=30)
    recall_curve = information_preservation.compute_recall_curve(keywords, final_content, cutoffs=(10, 20, 30))

    # G-Eval 점수 한 번만 돌리면 신뢰할 수 없다 — 같은 원고를 세 번 채점했더니
    # 0.00 / 0.70 / 0.90이 나온 실측 사례가 있다(2026-07-19). 0.00은 판정
    # 단계 응답이 한 번 깨진 것으로 보이는 이상치였지만, 0.70과 0.90도 편차가
    # 커서 "한 번 돌린 값"을 발표에 쓰면 안 된다 — 여러 번 돌려 평균±표준편차로
    # 보고한다.
    print(f"[3/4] G-Eval 서사일관성 채점 중({geval_runs}회 반복 후 평균)...", file=sys.stderr)
    judge = SolarJudgeModel()
    geval_scores: list[float] = []
    for i in range(geval_runs):
        try:
            score = await _score_coherence(judge, manuscript)
            if score is not None:
                geval_scores.append(score)
            print(f"    [{i + 1}/{geval_runs}] {score}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — 한 번 실패해도 나머지 회차로 평균을 낸다
            print(f"    [{i + 1}/{geval_runs}] 실패: {exc!r}", file=sys.stderr)
    coherence_stats = {
        "runs": geval_scores,
        "mean": statistics.mean(geval_scores) if geval_scores else None,
        "median": statistics.median(geval_scores) if geval_scores else None,
        "stdev": statistics.pstdev(geval_scores) if len(geval_scores) > 1 else None,
    }

    print("[4/4] 리포트 정리 중...", file=sys.stderr)
    return {
        "email": email,
        "title": manuscript.get("title"),
        "final_content_length": len(final_content),
        "phase2_distortion_detection": phase2_stats,
        "phase4_own_verification": phase4_own_stats,
        "information_preservation_recall": recall_curve,
        "narrative_coherence_geval": coherence_stats,
        "structure": structural_stats,
    }


def _print_summary(report: dict) -> None:
    print(f"\n=== 정량 지표 리포트 — {report['email']} ({report['title']}) ===\n")

    p2 = report["phase2_distortion_detection"]
    print("[Phase 2] 재조립 왜곡 탐지(Solar LLM 판정, solar-mini — 프로덕션 내장, 원래 로컬 NLI였으나 교체됨)")
    print(f"  세션 {p2['total_sessions']}개 중 왜곡 플래그 {p2['distortion_flagged_sessions']}건", end="")
    if p2["distortion_flagged_rate"] is not None:
        print(f" ({p2['distortion_flagged_rate']:.1%})")
    else:
        print()

    p4 = report["phase4_own_verification"]
    print("\n[Phase 4] 집필 시점 자체 검증(프로덕션 내장)")
    print(f"  챕터 {p4['chapter_count']}개")
    print(f"  · 팩트체크(결정론적 문자열 대조 — 이 시스템에서 유일하게 모델 의견이 아닌 신호): {p4['total_factcheck_flags']}건")
    print(f"  · 근거검증(LLM 판단, solar-pro3 1차 + solar-mini 2차 게이트): {p4['total_groundedness_flags']}건")
    if p4["flags_per_chapter"] is not None:
        print(f"  챕터당 평균 {p4['flags_per_chapter']:.2f}건")

    print("\n[정보보존율] 원본 핵심 키워드가 완성 원고에 살아남은 비율(생존 판정은 순수 문자열 매칭)")
    for cutoff, data in report["information_preservation_recall"].items():
        if data["recall"] is not None:
            print(f"  상위 {cutoff}개: {data['recall']:.1%} ({data['survived']}/{data['total']})")

    coherence = report["narrative_coherence_geval"]
    runs_str = ", ".join(f"{r:.1f}" for r in coherence["runs"])
    print(f"\n[G-Eval] 서사일관성(문장력·구조적 자연스러움) — {len(coherence['runs'])}회 반복: [{runs_str}]")
    print(
        f"  중앙값 {coherence['median']:.2f} (평균 {coherence['mean']:.2f} / 표준편차 {coherence['stdev']:.2f})"
        if coherence["mean"] is not None
        else "  실패"
    )

    s = report["structure"]
    print("\n[구조] Part/챕터 구성")
    print(f"  Part {s['part_count']}개, 챕터 {s['chapter_count']}개")
    if s["chapter_length_mean"] is not None:
        print(
            f"  챕터당 분량: 평균 {s['chapter_length_mean']:.0f}자 "
            f"(표준편차 {s['chapter_length_stdev']:.0f}, 범위 {s['chapter_length_min']}~{s['chapter_length_max']}자)"
            if s["chapter_length_stdev"] is not None
            else f"  챕터당 분량: 평균 {s['chapter_length_mean']:.0f}자"
        )


async def main(email: str, *, chapter_concurrency: int, regenerate: bool, geval_runs: int) -> None:
    report = await run_report(
        email, chapter_concurrency=chapter_concurrency, regenerate=regenerate, geval_runs=geval_runs
    )

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    local_part = email.split("@")[0]
    out_path = _RESULTS_DIR / f"quantitative_report_{local_part}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_summary(report)
    print(f"\n상세 결과: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="증명③(정량 지표 공개) — 이미 처리된 인물 1명의 완성 원고에 대한 정량 지표 리포트.")
    parser.add_argument("--email", required=True, help="scripts/seed_dummy.py + process_seeded_sessions.py로 이미 처리된 유저 이메일")
    parser.add_argument(
        "--chapter-concurrency", type=int, default=parallel_chapters.DEFAULT_CHAPTER_CONCURRENCY,
        help=f"챕터 동시 집필 수(기본 {parallel_chapters.DEFAULT_CHAPTER_CONCURRENCY}) — 기존 완성 원고가 있으면 쓰이지 않는다.",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="이미 완성된 원고가 있어도 무시하고 새로 생성한다(기본은 기존 원고 재사용).",
    )
    parser.add_argument(
        "--geval-runs",
        type=int,
        default=3,
        help="G-Eval 서사일관성을 몇 번 반복해 평균 낼지(기본 3) — 같은 원고로 0.00/0.70/0.90이 "
        "나온 실측 사례가 있어 한 번만 돌리면 신뢰할 수 없다.",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            args.email,
            chapter_concurrency=args.chapter_concurrency,
            regenerate=args.regenerate,
            geval_runs=args.geval_runs,
        )
    )
