"""
scripts/seed_dummy.py가 실제 Postgres DB에 삽입한 100개 세션은 ChatLog(답변)만
들어 있고 아직 아무 처리도 거치지 않은 상태다 — Phase 2(산문 재조립 → 왜곡 탐지
→ 이벤트 분할·라벨 추출)가 빠져 있다. 실제 앱에서는 세션이 끝나는 순간 Celery
태스크(app/workers/tasks.py:process_session_completion)가 이걸 자동으로 처리하지만,
시드 스크립트는 DB에 직접 삽입만 하므로 Celery 큐를 거치지 않는다.

이 스크립트는 그 빠진 단계를 채운다 — 지정한 이메일(들)의 유저가 가진, 아직
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
이 격리 덕분에 여러 인물의 세션을 하나의 작업 큐에 섞어도 안전하다(아래
"여러 인물 동시 처리" 참조) — 어느 세션이 실패해도 다른 세션(같은 인물이든
다른 인물이든)에 영향을 주지 않는다.

## 여러 인물 동시 처리 (--email에 쉼표로 나열, 2026-07-18 추가)

터미널을 인물 수만큼 띄우는 것보다 이 스크립트 하나에 여러 이메일을 넘기고
--concurrency로 동시 처리 수를 직접 통제하는 걸 권장한다. 원래 이 권장의
근거는 로컬 NLI 모델(app/clients/nli.py, transformers 기반)이 프로세스마다
각각 메모리에 로드돼 터미널 N개 = 모델 사본 N개로 컴퓨터가 버거워지는
문제였는데, 왜곡 탐지가 Solar LLM 판정(solar-mini)으로 교체되며 그 로컬
모델 자체가 사라져(2026-07-19) 이 특정 원인은 더 이상 해당하지 않는다.
다만 한 프로세스로 합치는 걸 여전히 권장하는 이유는 남아 있다 — 세션 100개를
완전 순차(동시성 1)로 처리하면 세션당 실측 30~70초가 그대로 곱해져 30~80분이
걸리고(2026-07-19 실사용 확인), 여러 인물을 각자 터미널에서 돌리면 로그가
따로 흩어지고 Upstage 요청 한도(429)도 프로세스마다 독립적으로 부딪혀 결국
전체적으로는 한 프로세스에서 --concurrency로 명시적으로 조절하는 쪽이
더 통제하기 쉽다.

실행 방법 (backend/ 디렉토리에서):
    # 한 명
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions --email "billgates@example.com"
    # 여러 명, 동시성 2로 (기본값은 1 = 순차)
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions \\
        --email "billgates@example.com,napoleon@example.com" --concurrency 2

위 트랜잭션 수정 이전 버전으로 이미 실행한 적이 있다면, 아래로 먼저 오염된
세션(산문은 있는데 이벤트가 0건인 세션)을 찾아 재처리 가능한 상태로 되돌린 뒤
다시 실행하라(--repair는 --email에 나열된 인물 전원에 대해 수행된다):
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions --email "billgates@example.com" --repair

## 왜곡 플래그된 세션 (2026-07-19 추가)

왜곡 탐지 실패는 버그가 아니라 process_completed_session이 예외 없이 정상
종료하는 경로다(산문은 저장하되 이벤트는 0건, distortion_flagged=True) —
그래서 이 스크립트는 그걸 "성공"으로 집계하고, session_prose가 이미 채워져
있으니 다음 실행에서도 미처리 목록에 다시 걸리지 않는다. 이제는 세션 처리
자체가(아래 "세션마다 끝까지 해결" 참조) 왜곡 플래그로 끝나는 걸 실패로
취급해 자동으로 재시도하지만, 이 스크립트를 바꾸기 *전에* 이미 플래그로
끝난 세션들은 그 대상이 아니므로 한 번 --retry-flagged로 되돌려야 한다:
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions --email "billgates@example.com" --retry-flagged

## 세션마다 끝까지 해결(플래그를 남긴 채 다음으로 넘어가지 않음, 2026-07-19)

왜곡 탐지 실패 시 event_extraction_service.process_completed_session 자체는
외과적 수리를 최대 _DISTORTION_REPAIR_MAX_PASSES회(현재 2회)만 시도하고 그래도
안 되면 플래그를 남기고 정상 종료한다 — 이건 프로덕션(Celery)에서 실제 사용자
세션이 진짜로 애매한 경우 무한정 API 비용을 쓰지 않고 사람의 검토("나의
이야기"에서 직접 수정)로 넘기기 위한 의도된 안전장치라, 그 쪽은 건드리지
않았다. 대신 이 스크립트는 그 결과를 받아서(_process_one_session_until_resolved)
플래그로 끝나면 session_prose를 초기화해 처음부터(재조립부터) 다시 시도한다 —
매 라운드가 새로 재조립하므로 이전 라운드와 다른 결과가 나올 여지가 있다.
_SESSION_RETRY_ROUNDS회(기본 5회, 라운드당 최대 3회 시도 = 최악의 경우 세션 하나에
최대 15회 시도)까지도 계속 플래그면 그제서야 "실패"로 집계한다 — 완전히
무한정 재시도하지는 않는다(한 세션이 정말로 수렴 안 되는 경우 API 비용이
무한정 나가는 걸 막는 안전판). 실패로 집계된 세션은 이 스크립트를 다시
실행하면(--repair나 --retry-flagged 없이) 자동으로 재시도된다.

주의: .env의 GATEWAY_BACKEND=postgres를 그대로 쓴다(evals/run_benchmark.py와
달리 mock으로 강제 전환하지 않음) — seed_dummy.py가 심어둔 실제 데이터를 대상으로
해야 하므로 의도적으로 실제 DB에 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.gateways.factory import gateways_context
from app.services import event_extraction_service

_STAGE_TIMEOUT_SECONDS = 600  # evals/run_benchmark.py와 동일한 이유(간헐적 지연 방어).

# 여러 인물을 동시에 처리하면 Upstage 요청 한도(429)에 걸리기 쉽다
# (evals/followup_trigger_audit.py에서 동시성 5만으로도 32/100이 429로 실패한
# 실측 사례, 2026-07-18). max_retries=0(app/clients/base.py)이라 SDK가 자동
# 재시도하지 않으므로 여기서 지수 백오프로 직접 재시도한다.
_RETRYABLE_MAX_ATTEMPTS = 4
_RETRYABLE_BASE_DELAY_SECONDS = 5.0
# 지터 폭(초) — 동시 처리 중 여러 세션이 같은 순간 429를 맞으면 지터 없는 고정
# 백오프는 재시도도 똑같은 타이밍에 몰려 두 번째 429 무더기를 만든다("thundering
# herd", 2026-07-19). 다만 이건 재시도가 서로 겹치는 것만 완화할 뿐, 동시 요청
# 자체가 Solar의 실제 처리량 한도를 넘어서는 근본 문제는 해결하지 않는다 —
# 동시성 값(--concurrency)을 보수적으로 잡아야 하는 이유이기도 하다.
_RETRY_JITTER_SECONDS = 3.0

# 순차 실행(기존 단일 인물 사용법)과 동일한 기본 동작을 유지하기 위해 1로 둔다 —
# 여러 인물을 동시에 처리하려면 --concurrency를 명시적으로 올려야 한다.
_DEFAULT_CONCURRENCY = 1

# 세션이 왜곡 플래그로 끝나면 산문을 초기화해 처음부터 다시 시도하는 최대 라운드 수
# (모듈 docstring "세션마다 끝까지 해결" 참조). 완전히 무한 재시도로 두지 않는 이유는
# 한 세션이 정말로 수렴 안 될 때 API 비용이 무한정 나가는 걸 막기 위해서다.
_SESSION_RETRY_ROUNDS = 5


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
            delay = _RETRYABLE_BASE_DELAY_SECONDS * (2**attempt) + random.uniform(0, _RETRY_JITTER_SECONDS)
            print(
                f"    [429 재시도 {attempt + 1}/{_RETRYABLE_MAX_ATTEMPTS}] {delay:.1f}초 대기 "
                f"(session={session_id})...",
                file=sys.stderr,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def _process_one_session_until_resolved(session_id: uuid.UUID) -> int:
    """_process_one_session이 예외 없이 반환해도, 그 결과가 왜곡 플래그(산문은
    저장됐지만 이벤트 0건)로 끝났다면 다음 세션으로 넘어가지 않고 이 세션을
    끝까지 해결한다(모듈 docstring "세션마다 끝까지 해결" 참조). 산문을 다시
    None으로 되돌려 처음부터(재조립부터) 재시도한다 — 매 라운드가 새로 재조립
    하므로 이전 라운드와 다른 결과가 나올 여지가 있다(라운드 안에서는
    event_extraction_service._DISTORTION_REPAIR_MAX_PASSES회의 국소 수리도
    거친다). _SESSION_RETRY_ROUNDS회까지도 계속 플래그면 실패로 취급해 예외를
    던진다 — 완전한 무한 재시도는 아니다. 마지막에도 session_prose를 None으로
    되돌려두고 실패 처리한다 — --repair/--retry-flagged 없이 이 스크립트를
    그냥 다시 실행해도(다른 실패 유형과 동일하게) 자동으로 재시도되게 하기
    위함이다."""
    last_count = 0
    for round_num in range(1, _SESSION_RETRY_ROUNDS + 1):
        last_count = await _process_one_session(session_id)
        async with gateways_context() as gateways:
            session = await gateways.sessions.get_by_id(session_id)
        if not session.distortion_flagged:
            return last_count
        verb = "다시 시도합니다" if round_num < _SESSION_RETRY_ROUNDS else "포기하고 실패 처리합니다"
        print(
            f"    [세션 재시도 {round_num}/{_SESSION_RETRY_ROUNDS}] session={session_id} "
            f"왜곡 플래그로 종료 — 산문을 초기화하고 {verb}...",
            file=sys.stderr,
        )
        async with gateways_context() as gateways:
            await gateways.sessions.set_session_prose(session_id, None)  # type: ignore[arg-type]
            await gateways.sessions.set_distortion_flagged(session_id, False)
            await gateways.commit()
    raise RuntimeError(
        f"session={session_id}: {_SESSION_RETRY_ROUNDS}회 재시도 후에도 왜곡 플래그로 종료됨 "
        "(원본 발화 보존이 검증되지 않아 이벤트 추출을 계속 보류함 — session_prose는 "
        "None으로 되돌려뒀으므로 다시 실행하면 자동으로 재시도됨)"
    )


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


async def _retry_flagged_sessions(user_id: uuid.UUID) -> int:
    """distortion_flagged=True인 세션의 session_prose를 다시 None으로 되돌려
    미처리 목록에 다시 걸리게 한다 — _repair_contaminated_sessions와 달리 이건
    "버그로 오염된 상태"가 아니라 "정상적으로 왜곡 플래그된 상태"를 대상으로
    하므로 의도적으로 분리했다(모듈 docstring "왜곡 플래그된 세션" 참조). 이
    스크립트를 다시 돌리면 새로 추가된 _process_one_session_until_resolved가
    처리하므로, 이번에는 플래그로 끝나도 자동으로 재시도된다."""
    async with gateways_context() as gateways:
        sessions = await gateways.sessions.list_by_user(user_id)
        retried = 0
        for session in sessions:
            if not session.distortion_flagged:
                continue
            await gateways.sessions.set_session_prose(session.id, None)  # type: ignore[arg-type]
            await gateways.sessions.set_distortion_flagged(session.id, False)
            retried += 1
            print(
                f"  [재시도 대상] session={session.id} — distortion_flagged 해제, 재처리 대상으로 되돌림",
                file=sys.stderr,
            )
        await gateways.commit()
        return retried


async def _resolve_user(email: str) -> tuple[uuid.UUID, str] | None:
    async with gateways_context() as gateways:
        user = await gateways.users.get_by_email(email)
        if user is None:
            print(f"  [경고] {email}에 해당하는 유저가 없습니다 — 건너뜀(seed_dummy.py를 먼저 실행하세요).", file=sys.stderr)
            return None
        return user.id, user.name


async def _collect_pending(emails: list[str]) -> list[tuple[str, uuid.UUID]]:
    """emails 각각의 미처리 세션을 (이메일, session_id) 쌍으로 한데 모은다 —
    인물이 다르다는 정보는 로그 표시에만 쓰고, 처리 자체는 인물 구분 없이
    _process_one_session(session_id)에 그대로 넘긴다(세션 단위 격리라 안전)."""
    work: list[tuple[str, uuid.UUID]] = []
    for email in emails:
        resolved = await _resolve_user(email)
        if resolved is None:
            continue
        user_id, user_name = resolved
        async with gateways_context() as gateways:
            sessions = await gateways.sessions.list_by_user(user_id)
        pending = [s.id for s in sessions if s.session_prose is None]
        print(f"  [{user_name} ({email})] 세션 {len(sessions)}개 중 미처리 {len(pending)}개", file=sys.stderr)
        work.extend((email, sid) for sid in pending)
    return work


async def _run_repair(emails: list[str]) -> None:
    for email in emails:
        resolved = await _resolve_user(email)
        if resolved is None:
            continue
        user_id, user_name = resolved
        repaired = await _repair_contaminated_sessions(user_id)
        print(f"[{user_name} ({email})] {repaired}건을 재처리 대상으로 되돌렸습니다.")
    print("\n복구 완료. --repair 없이 다시 실행하세요.")


async def _run_retry_flagged(emails: list[str]) -> None:
    for email in emails:
        resolved = await _resolve_user(email)
        if resolved is None:
            continue
        user_id, user_name = resolved
        retried = await _retry_flagged_sessions(user_id)
        print(f"[{user_name} ({email})] {retried}건을 재처리 대상으로 되돌렸습니다.")
    print("\n되돌리기 완료. --retry-flagged 없이 다시 실행하세요.")


async def main(emails: list[str], *, repair: bool, retry_flagged: bool, concurrency: int) -> None:
    if repair:
        await _run_repair(emails)
        return
    if retry_flagged:
        await _run_retry_flagged(emails)
        return

    work = await _collect_pending(emails)
    print(f"\n[전체] {len(emails)}명, 총 미처리 {len(work)}세션, 동시성={concurrency}")
    if not work:
        return

    semaphore = asyncio.Semaphore(concurrency)
    counters = {"processed": 0, "failed": 0}
    counters_lock = asyncio.Lock()
    start_time = time.monotonic()

    async def _report_progress() -> None:
        """완료(성공+실패) 건수 기준 평균 처리 시간으로 ETA를 낸다 — 실측 소요시간을
        미리 알 수 없다는 문제(2026-07-18 사용자 질문)에 대한 답. 처음 몇 건은
        평균이 안정되기 전이라 부정확할 수 있어, 완료 10건 이후부터 표시한다."""
        done = counters["processed"] + counters["failed"]
        if done < 10:
            return
        elapsed = time.monotonic() - start_time
        # elapsed/done은 동시성이 이미 반영된 "체감 처리 간격"이다(동시성 3이면
        # 실제 세션 하나가 15초 걸려도 3개씩 겹쳐 돌아 5초/건 꼴로 완료됨) —
        # 그래서 남은 시간 추정에 concurrency로 다시 나누면 안 된다.
        avg_interval = elapsed / done
        remaining = len(work) - done
        eta_seconds = avg_interval * remaining
        print(
            f"    [진행] {done}/{len(work)} 완료 ({elapsed / 60:.1f}분 경과, "
            f"체감 {avg_interval:.1f}초/건) — 남은 {remaining}건 예상 소요 약 {eta_seconds / 60:.0f}분",
            file=sys.stderr,
        )

    async def _worker(index: int, email: str, session_id: uuid.UUID) -> None:
        async with semaphore:
            print(f"  [{index}/{len(work)}] {email} session={session_id} 처리 중...", file=sys.stderr)
            try:
                count = await _process_one_session_until_resolved(session_id)
                print(f"    → {email} 이벤트 {count}건 추출", file=sys.stderr)
                async with counters_lock:
                    counters["processed"] += 1
            except Exception as exc:  # noqa: BLE001 — 세션 하나 실패해도 나머지는 계속(자기 트랜잭션만 롤백됨)
                print(f"    ❌ {email} 실패: {exc!r}", file=sys.stderr)
                async with counters_lock:
                    counters["failed"] += 1
            await _report_progress()

    await asyncio.gather(*(_worker(i, email, sid) for i, (email, sid) in enumerate(work, start=1)))

    total_elapsed = time.monotonic() - start_time
    print(f"\n총 소요 시간: {total_elapsed / 60:.1f}분")

    print(f"\n완료: {counters['processed']}건 처리, {counters['failed']}건 실패 (전체 {len(work)}건)")
    if counters["failed"]:
        print("실패한 세션은 session_prose가 None으로 남아 있으므로, 이 스크립트를 --repair 없이 그냥 다시 실행하면 자동으로 재시도됩니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="seed_dummy.py로 심은 세션들의 Phase 2(이벤트 추출)를 일괄 실행합니다.")
    parser.add_argument("--email", required=True, help="seed_dummy.py로 시딩한 유저의 이메일(쉼표로 여러 명 나열 가능)")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=_DEFAULT_CONCURRENCY,
        help=f"동시에 처리할 세션 수(기본 {_DEFAULT_CONCURRENCY}=순차). 여러 인물을 한 프로세스에서 동시 처리할 때 올린다.",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="구버전 스크립트로 실행해 오염된(산문은 있는데 이벤트 0건) 세션을 재처리 대상으로 되돌립니다(--email에 나열된 인물 전원 대상).",
    )
    parser.add_argument(
        "--retry-flagged",
        action="store_true",
        help="이 스크립트의 '세션마다 끝까지 해결' 기능이 추가되기 전에 왜곡 플래그로 끝난 세션을 재처리 대상으로 되돌립니다(--email에 나열된 인물 전원 대상).",
    )
    args = parser.parse_args()
    emails = [e.strip() for e in args.email.split(",") if e.strip()]
    asyncio.run(
        main(emails, repair=args.repair, retry_flagged=args.retry_flagged, concurrency=args.concurrency)
    )
