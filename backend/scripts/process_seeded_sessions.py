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

실행 방법 (backend/ 디렉토리에서):
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions --email "billgates@example.com"

주의: .env의 GATEWAY_BACKEND=postgres를 그대로 쓴다(evals/run_benchmark.py와
달리 mock으로 강제 전환하지 않음) — seed_dummy.py가 심어둔 실제 데이터를 대상으로
해야 하므로 의도적으로 실제 DB에 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.gateways.factory import gateways_context
from app.services import event_extraction_service

_STAGE_TIMEOUT_SECONDS = 120  # evals/run_benchmark.py와 동일한 이유(간헐적 지연 방어).


async def main(email: str) -> None:
    async with gateways_context() as gateways:
        user = await gateways.users.get_by_email(email)
        if user is None:
            print(f"❌ 이메일 {email}에 해당하는 유저가 없습니다. seed_dummy.py를 먼저 실행하세요.")
            sys.exit(1)

        sessions = await gateways.sessions.list_by_user(user.id)
        pending = [s for s in sessions if s.session_prose is None]
        print(f"[대상] {user.name} ({email}) — 세션 {len(sessions)}개 중 미처리 {len(pending)}개")

        processed = 0
        failed = 0
        for i, session in enumerate(pending, start=1):
            print(f"  [{i}/{len(pending)}] session={session.id} 처리 중...", file=sys.stderr)
            try:
                events = await asyncio.wait_for(
                    event_extraction_service.process_completed_session(gateways, session.id),
                    timeout=_STAGE_TIMEOUT_SECONDS,
                )
                await gateways.commit()
                print(f"    → 이벤트 {len(events)}건 추출", file=sys.stderr)
                processed += 1
            except Exception as exc:  # noqa: BLE001 — 세션 하나 실패해도 나머지는 계속
                print(f"    ❌ 실패: {exc!r}", file=sys.stderr)
                failed += 1

        print(f"\n완료: {processed}건 처리, {failed}건 실패 (전체 {len(pending)}건)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="seed_dummy.py로 심은 세션들의 Phase 2(이벤트 추출)를 일괄 실행합니다.")
    parser.add_argument("--email", required=True, help="seed_dummy.py로 시딩한 유저의 이메일")
    args = parser.parse_args()
    asyncio.run(main(args.email))
