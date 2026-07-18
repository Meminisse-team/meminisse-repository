"""
scripts/seed_dummy.py가 실제 Postgres DB에 삽입한 100개 세션은 ChatLog(답변)만
들어 있고 아직 아무 처리도 거치지 않은 상태다 — Phase 2(산문 재조립 → 왜곡 탐지
→ 이벤트 분할·라벨 추출)가 빠져 있다. 실제 앱에서는 세션이 끝나는 순간 Celery
태스크(app/workers/tasks.py:process_session_completion)가 이걸 자동으로 처리하지만,
시드 스크립트는 DB에 직접 삽입만 하므로 Celery 큐를 거치지 않는다.

이 스크립트는 그 빠진 단계를 채운다 — 지정한 이메일의 유저가 가진, 아직
session_prose가 없는 모든 세션에 대해 실제 프로덕션 함수
event_extraction_service.process_completed_session을 그대로 호출한다(테스트용
대역이 아니라 진짜 운영 코드 경로 — Celery 태스크 본체가 호출하는 것과 동일한
함수). 실제 Upstage API를 호출하므로 100세션 기준 비용·시간이 발생한다.

**세션마다 독립된 트랜잭션을 쓴다(2026-07-18, 공유 트랜잭션 버그 수정)** —
원래는 for 루프 전체가 gateways_context() 하나를 공유해서, 세션 하나가 이벤트
추출 도중 실패해도 그 세션의 set_session_prose flush가 롤백되지 않고 남아있다가
"다음에 성공한 세션의 commit()"에 함께 실려 나가는 문제가 실사용 중 확인됐다 —
그 결과 "실패"로 로그된 세션이 실제로는 session_prose만 채워지고 이벤트는 0건인
채로 저장되고, 재실행 시 "session_prose is not None"이라 미처리 목록에서 영영
빠져 재시도되지 않았다. 지금은 세션마다 async with gateways_context()를 새로
열어, 실패 시 그 세션의 변경만 롤백되고 다른 세션에 전혀 영향을 주지 않는다.

실행 방법 (backend/ 디렉토리에서):
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions --email "billgates@example.com"

위 수정 이전 버전으로 이미 실행한 적이 있다면, 아래로 먼저 오염된 세션(산문은
있는데 이벤트가 0건인 세션)을 찾아 재처리 가능한 상태로 되돌린 뒤 다시 실행하라:
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions --email "billgates@example.com" --repair

주의: .env의 GATEWAY_BACKEND=postgres를 그대로 쓴다(evals/run_benchmark.py와
달리 mock으로 강제 전환하지 않음) — seed_dummy.py가 심어둔 실제 데이터를 대상으로
해야 하므로 의도적으로 실제 DB에 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.gateways.factory import gateways_context
from app.services import event_extraction_service

_STAGE_TIMEOUT_SECONDS = 120  # evals/run_benchmark.py와 동일한 이유(간헐적 지연 방어).

# 여러 인물을 동시에(터미널 여러 개로) 처리하면 Upstage 요청 한도(429)에 걸리기
# 쉽다(evals/followup_trigger_audit.py에서 동시성 5만으로도 32/100이 429로
# 실패한 실측 사례, 2026-07-18). max_retries=0(app/clients/base.py)이라 SDK가
# 자동 재시도하지 않으므로 여기서 지수 백오프로 직접 재시도한다.
_RETRYABLE_MAX_ATTEMPTS = 4
_RETRYABLE_BASE_DELAY_SECONDS = 5.0


async def _process_one_session(session_id: uuid.UUID) -> int:
    """세션 하나를 독립된 트랜잭션으로 처리한다 — 모듈 docstring "세션마다
    독립된 트랜잭션을 쓴다" 참조. 실패 시 이 함수의 gateways_context()만
    롤백되고 예외가 호출부로 전파된다. 429(요청 한도)는 재시도하고, 그 외
    예외는 즉시 호출부로 전파한다(호출부가 "실패"로 기록 후 다음 세션 진행)."""
    last_exc: Exception | None = None
    for attempt in range(_RETRYABLE_MAX_ATTEMPTS):
        try:
            async with gateways_context() as gateways:
                events = await asyncio.wait_for(
                    event_extraction_service.process_completed_session(gateways, session_id),
                    timeout=_STAGE_TIMEOUT_SECONDS,
                )
                await gateways.commit()
                return len(events)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if "429" not in str(exc) and "too_many_requests" not in str(exc):
                raise
            delay = _RETRYABLE_BASE_DELAY_SECONDS * (2**attempt)
            print(f"    [429 재시도 {attempt + 1}/{_RETRYABLE_MAX_ATTEMPTS}] {delay:.0f}초 대기...", file=sys.stderr)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def _repair_contaminated_sessions(user_id: uuid.UUID) -> int:
    """구버전(공유 트랜잭션) 실행으로 "산문은 있는데 이벤트 0건, distortion_flagged도
    아님"인 오염된 세션을 찾아 session_prose를 다시 None으로 되돌린다 — 그래야
    다음 실행의 "미처리" 판정(session_prose is None)에 다시 걸려 재처리된다.

    distortion_flagged=True인 세션은 건드리지 않는다 — 그건 왜곡 탐지가 실제로
    재시도까지 실패해 의도적으로 보류된 정상 상태(모듈 event_extraction_service.
    process_completed_session 참조)이지, 이 버그의 증상이 아니다."""
    async with gateways_context() as gateways:
        sessions = await gateways.sessions.list_by_user(user_id)
        repaired = 0
        for session in sessions:
            if session.session_prose is None or session.distortion_flagged:
                continue
            events = await gateways.events.list_by_session(session.id)
            if events:
                continue  # 정상 처리된 세션 — 건드리지 않는다.
            await gateways.sessions.set_session_prose(session.id, None)  # type: ignore[arg-type]
            repaired += 1
            print(f"  [복구] session={session.id} — session_prose를 초기화해 재처리 대상으로 되돌림", file=sys.stderr)
        await gateways.commit()
        return repaired


async def main(email: str, *, repair: bool) -> None:
    async with gateways_context() as gateways:
        user = await gateways.users.get_by_email(email)
        if user is None:
            print(f"❌ 이메일 {email}에 해당하는 유저가 없습니다. seed_dummy.py를 먼저 실행하세요.")
            sys.exit(1)
        user_id = user.id
        user_name = user.name

    if repair:
        repaired = await _repair_contaminated_sessions(user_id)
        print(f"\n복구 완료: {repaired}건을 재처리 대상으로 되돌렸습니다. --repair 없이 다시 실행하세요.")
        return

    async with gateways_context() as gateways:
        sessions = await gateways.sessions.list_by_user(user_id)
        pending_ids = [s.id for s in sessions if s.session_prose is None]

    print(f"[대상] {user_name} ({email}) — 세션 {len(sessions)}개 중 미처리 {len(pending_ids)}개")

    processed = 0
    failed = 0
    for i, session_id in enumerate(pending_ids, start=1):
        print(f"  [{i}/{len(pending_ids)}] session={session_id} 처리 중...", file=sys.stderr)
        try:
            count = await _process_one_session(session_id)
            print(f"    → 이벤트 {count}건 추출", file=sys.stderr)
            processed += 1
        except Exception as exc:  # noqa: BLE001 — 세션 하나 실패해도 나머지는 계속(자기 트랜잭션만 롤백됨)
            print(f"    ❌ 실패: {exc!r}", file=sys.stderr)
            failed += 1

    print(f"\n완료: {processed}건 처리, {failed}건 실패 (전체 {len(pending_ids)}건)")
    if failed:
        print("실패한 세션은 session_prose가 None으로 남아 있으므로, 이 스크립트를 --repair 없이 그냥 다시 실행하면 자동으로 재시도됩니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="seed_dummy.py로 심은 세션들의 Phase 2(이벤트 추출)를 일괄 실행합니다.")
    parser.add_argument("--email", required=True, help="seed_dummy.py로 시딩한 유저의 이메일")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="구버전 스크립트로 실행해 오염된(산문은 있는데 이벤트 0건) 세션을 재처리 대상으로 되돌립니다.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.email, repair=args.repair))
