"""
꼬리질문 테스트 B — 실제 유명인 100문항 더미 데이터로 "꼬리질문 발동 빈도" 실측.

기획안·사업화 방안이 핵심 차별점으로 내세우는 꼬리질문 메커니즘은
interview_service.add_user_turn(app/services/interview_service.py:362)에
3단계로 구현돼 있다:
  1) 필수 슬롯 미충족형 — 시기·장소 등 핵심 정보 누락
  2) 풍부함(분량) 부족형 — 슬롯은 찼지만 답변이 너무 짧음(경쟁사 "레멘토"의
     약점 — 기획안 사업화 방안 1.1절 — 을 정확히 겨냥하는 부분)
  3) 맥락 기반형 — 슬롯·분량 다 충족돼도 "진짜 전기 작가라면 캐물었을 지점"을
     LLM이 판단

테스트 A(evals/baseline_and_ablations.py의 no_followup 어블레이션, 합성
페르소나 전용)는 "꼬리질문이 있고 없고가 최종 결과 품질에 미치는 영향"을 잰다.
이 스크립트(테스트 B)는 그와 다른 질문 — "실제 역사적 인물들의 답변이 우리
시스템 앞에 던져지면 몇 %가 꼬리질문으로 이어지는가, 어떤 유형으로"를 실측
한다. 라이브 대화가 필요 없다 — add_user_turn의 판정 로직(_run_turn_gating,
_generate_followup_question 등)은 DB 세션 객체 없이도 순수 텍스트 판정
함수라 재사용 가능하다.

**DB 시딩이 필요 없다** — scripts/seed_dummy.py와 동일한 파서
(scripts.seed_dummy.parse_dummy_data)로 .txt 파일을 직접 읽어 각 답변을 "이
질문에 방금 막 답했다"는 새 세션의 첫 발화인 것처럼 판정 함수에 넣는다(실제
seed_dummy.py가 세션을 "질문 하나 = 세션 하나"로 만드는 것과 동일한 전제 —
InterviewSessionRecord 모델 docstring 참조). Postgres도, Supabase Auth 계정도
필요 없어 evals/real_data_comparison.py보다 훨씬 가볍고 빠르다.

**한 가지 근사**: 실제 라이브 세션은 오프닝 질문(assistant 첫 턴)이
session.chat_logs[0]에 이미 들어있지만, 여기서는 그 턴 없이 답변만 넣는다 —
맥락 기반 꼬리질문 판정(_generate_contextual_followup)이 오프닝 질문의 정확한
문구까지 참고하진 못한다는 뜻이다. 발동 여부 자체(있다/없다)에는 큰 영향이
없을 것으로 보이나, 완전히 동일하진 않다는 점을 밝혀둔다.

실행 (backend/ 디렉토리에서):
    ..\\venv\\Scripts\\python -m evals.followup_trigger_audit --file "C:\\...\\빌게이츠(v2).txt" --name "빌 게이츠"

결과: evals/results/followup_audit_<파일명>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents import prompts
from app.gateways.dto import InterviewSessionRecord
from app.models.enums import MessageRole, SessionStatus, SessionType
from app.services import interview_service
from scripts.seed_dummy import parse_dummy_data

_RESULTS_DIR = Path(__file__).parent / "results"
_STAGE_TIMEOUT_SECONDS = 60

CATEGORY_CRISIS = "위기_감지"
CATEGORY_BUFFER = "완충_응답(강한_부정감정)"
CATEGORY_SLOT = "필수슬롯형_꼬리질문"
CATEGORY_LENGTH = "분량부족형_꼬리질문"
CATEGORY_CONTEXTUAL = "맥락기반형_꼬리질문"
CATEGORY_NONE = "발동없음"


def _fresh_session() -> InterviewSessionRecord:
    """실제 DB 세션 없이 판정 함수들이 요구하는 최소 필드만 채운 대역. "질문
    하나 = 세션 하나" 관례(seed_dummy.py와 동일)를 따라 매 답변마다 새로 만든다
    — chat_logs=[]가 핵심(_total_user_content_length/_generate_contextual_
    followup이 "이 세션에서 지금까지 쓴 글자 수/오간 대화"를 이걸로 계산한다)."""
    now = datetime.now(timezone.utc)
    return InterviewSessionRecord(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session_type=SessionType.FIXED_QUESTION,
        question_id=None,
        linked_media_asset_id=None,
        status=SessionStatus.OPEN,
        slots_filled={key: False for key in prompts.ALL_SLOTS},
        followup_count=0,
        is_must_include=False,
        session_prose=None,
        started_at=now,
        completed_at=None,
        chat_logs=[],
    )


async def classify_answer(question: str, answer: str) -> dict[str, Any]:
    """add_user_turn(app/services/interview_service.py:362)의 판정 분기를 DB
    쓰기 없이 그대로 재현한다 — 실제 함수를 호출하지 않고 로직을 복제한 이유는
    add_user_turn이 세션 완료·Celery 큐잉 등 DB 부수효과를 전제로 하기 때문
    (모듈 docstring 참조: 판정과 DB 쓰기가 이미 두 단계로 분리돼 있어, 여기서는
    "판정" 절반만 그대로 재사용한다: _run_turn_gating, _generate_followup_
    question 등은 실제 프로덕션 함수를 그대로 호출한다)."""
    session = _fresh_session()

    if prompts.contains_crisis_keyword(answer):
        return {"category": CATEGORY_CRISIS, "followup_question": None}

    gating = await asyncio.wait_for(
        interview_service._run_turn_gating(content=answer, slots_filled=session.slots_filled),
        timeout=_STAGE_TIMEOUT_SECONDS,
    )
    if gating.get("strong_negative_emotion"):
        return {"category": CATEGORY_BUFFER, "followup_question": None}

    newly_filled = gating.get("newly_filled_slots", [])
    updated_slots = {**session.slots_filled, **{slot: True for slot in newly_filled}}
    missing_required = [key for key in prompts.REQUIRED_SLOTS if not updated_slots.get(key)]

    if missing_required:
        question_text = await asyncio.wait_for(
            interview_service._generate_followup_question(
                event_summary=answer, missing_required_slots=missing_required, followup_count=0
            ),
            timeout=_STAGE_TIMEOUT_SECONDS,
        )
        return {"category": CATEGORY_SLOT, "missing_slots": missing_required, "followup_question": question_text}

    if interview_service._total_user_content_length(session, answer) < prompts.MIN_RICH_ANSWER_LENGTH:
        question_text = await asyncio.wait_for(
            interview_service._generate_elaboration_question(answer), timeout=_STAGE_TIMEOUT_SECONDS
        )
        return {"category": CATEGORY_LENGTH, "answer_length": len(answer), "followup_question": question_text}

    contextual_question = await asyncio.wait_for(
        interview_service._generate_contextual_followup(session=session, latest_content=answer),
        timeout=_STAGE_TIMEOUT_SECONDS,
    )
    if contextual_question is not None:
        return {"category": CATEGORY_CONTEXTUAL, "followup_question": contextual_question}

    return {"category": CATEGORY_NONE, "followup_question": None}


_RETRYABLE_MAX_ATTEMPTS = 4
_RETRYABLE_BASE_DELAY_SECONDS = 5.0


async def _classify_with_retry(question: str, answer: str) -> dict[str, Any]:
    """app/clients/base.py가 max_retries=0으로 SDK 자동 재시도를 꺼둔 프로젝트라
    (다른 종류의 지연 문제에 대한 방어 — 해당 파일 참조), 이 스크립트가 100문항을
    동시에 던지면 Upstage 요청 한도(429 RateLimitError)에 그대로 걸린다(실측,
    2026-07-18 — concurrency=5로 처음 돌렸을 때 32/100이 429로 실패). 속도 제한은
    "느려서 멈춘 것"과 달리 잠깐 쉬었다 다시 보내면 성공하는 종류라, 여기서만
    지수 백오프 재시도를 추가한다."""
    last_exc: Exception | None = None
    for attempt in range(_RETRYABLE_MAX_ATTEMPTS):
        try:
            return await classify_answer(question, answer)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if "429" not in str(exc) and "too_many_requests" not in str(exc):
                raise
            delay = _RETRYABLE_BASE_DELAY_SECONDS * (2**attempt)
            print(f"    [429 재시도 {attempt + 1}/{_RETRYABLE_MAX_ATTEMPTS}] {delay:.0f}초 대기...", file=sys.stderr)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def run_audit(qa_pairs: list[dict], *, concurrency: int = 2) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any] | None] = [None] * len(qa_pairs)

    async def _worker(index: int, pair: dict) -> None:
        async with semaphore:
            try:
                classification = await _classify_with_retry(pair["question"], pair["answer"])
            except Exception as exc:  # noqa: BLE001 — 한 문항 실패해도 나머지는 계속
                print(f"  [실패] 질문 {pair['number']}: {exc!r}", file=sys.stderr)
                classification = {"category": "판정_실패", "error": repr(exc), "followup_question": None}
            results[index] = {
                "number": pair["number"],
                "question": pair["question"],
                "answer": pair["answer"],
                **classification,
            }
            print(f"  [{index + 1}/{len(qa_pairs)}] 질문 {pair['number']} → {classification['category']}", file=sys.stderr)

    await asyncio.gather(*(_worker(i, pair) for i, pair in enumerate(qa_pairs)))
    return results  # type: ignore[return-value]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    total = len(results)
    followup_categories = {CATEGORY_SLOT, CATEGORY_LENGTH, CATEGORY_CONTEXTUAL}
    followup_total = sum(counts.get(c, 0) for c in followup_categories)
    return {
        "total": total,
        "counts": counts,
        "followup_trigger_rate": followup_total / total if total else None,
        "by_type_rate": {c: counts.get(c, 0) / total for c in followup_categories} if total else {},
    }


async def main(file_path: Path, *, name: str) -> None:
    qa_pairs = parse_dummy_data(file_path)
    if not qa_pairs:
        print(f"❌ {file_path}에서 파싱된 질문-답변 쌍이 없습니다.")
        sys.exit(1)
    print(f"[대상] {name} — {len(qa_pairs)}개 답변 판정 시작", file=sys.stderr)

    results = await run_audit(qa_pairs)
    summary = summarize(results)

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _RESULTS_DIR / f"followup_audit_{file_path.stem}.json"
    out_path.write_text(
        json.dumps({"name": name, "summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n=== {name} 꼬리질문 발동 감사 (n={summary['total']}) ===")
    for category, count in sorted(summary["counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {category:30s} {count:3d}건 ({count / summary['total']:.0%})")
    print(f"\n꼬리질문 전체 발동률: {summary['followup_trigger_rate']:.0%}")
    print(f"상세 결과: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="100문항 더미 데이터로 꼬리질문 발동 빈도를 실측합니다(DB 불필요).")
    parser.add_argument("--file", required=True, help="더미 데이터 .txt 파일 경로")
    parser.add_argument("--name", required=True, help="인물 이름(리포트 표시용)")
    args = parser.parse_args()
    asyncio.run(main(Path(args.file), name=args.name))
