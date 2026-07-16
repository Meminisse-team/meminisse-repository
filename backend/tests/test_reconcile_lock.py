"""reconcile_stale_sessions 중복 실행 방지 락(app/workers/tasks.py) 테스트.

배경: 2026-07-16 실사용 중 Redis 컨테이너가 죽었다가 재기동된 직후 Celery
Beat가 이 태스크를 짧은 시간 안에 여러 번(관찰상 8회 이상) 몰아서 재발행했고,
매 실행이 그 시점의 모든 "처리 지연" 세션을 통째로 재큐잉하면서 완료 세션
100개가 평균 6배(총 622개 메시지)까지 중복 큐잉되는 사고로 이어졌다. Redis
SET NX EX 기반 락으로, 같은 TTL 창 안에서는 실제 재조정 작업(조회 + 재큐잉)이
정확히 한 번만 일어나야 한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.workers import tasks


class _FakeRedisClient:
    def __init__(self, *, set_result: bool) -> None:
        self._set_result = set_result
        self.set_calls: list[tuple] = []
        self.closed = False

    async def set(self, key, value, *, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        return self._set_result

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_acquire_reconcile_lock_true_when_key_not_already_set() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    with patch("app.workers.tasks.redis_asyncio.from_url", return_value=fake_client):
        acquired = await tasks._acquire_reconcile_lock()

    assert acquired is True
    assert fake_client.set_calls == [
        (tasks._RECONCILE_LOCK_KEY, "1", True, tasks._RECONCILE_LOCK_TTL_SECONDS)
    ]
    assert fake_client.closed is True  # 락 클라이언트 연결을 매번 정리하는지 확인


@pytest.mark.asyncio
async def test_acquire_reconcile_lock_false_when_key_already_set() -> None:
    """다른 실행이 이미 락을 쥐고 있으면(TTL 안 지남) 획득 실패해야 한다."""
    fake_client = _FakeRedisClient(set_result=False)
    with patch("app.workers.tasks.redis_asyncio.from_url", return_value=fake_client):
        acquired = await tasks._acquire_reconcile_lock()

    assert acquired is False


@pytest.mark.asyncio
async def test_reconcile_async_skips_actual_work_when_lock_not_acquired() -> None:
    """락 획득 실패 시 admin_service.reconcile_stale_sessions(DB 조회+재큐잉)
    자체가 아예 호출되지 않아야 한다 — 이게 중복 재큐잉 사고를 막는 핵심 계약."""
    with (
        patch("app.workers.tasks._acquire_reconcile_lock", new=AsyncMock(return_value=False)),
        patch("app.services.admin_service.reconcile_stale_sessions", new=AsyncMock()) as mocked,
    ):
        await tasks._reconcile_stale_sessions_async()

    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_async_runs_actual_work_when_lock_acquired() -> None:
    """정상적으로 락을 획득했을 때는(=중복 호출이 아닐 때는) 평소대로 실제
    재조정 작업이 실행돼야 한다 — 락이 정상 5분 주기 실행 자체를 막으면 안 됨."""
    with (
        patch("app.workers.tasks._acquire_reconcile_lock", new=AsyncMock(return_value=True)),
        patch(
            "app.services.admin_service.reconcile_stale_sessions", new=AsyncMock(return_value=3)
        ) as mocked,
    ):
        await tasks._reconcile_stale_sessions_async()

    mocked.assert_called_once()
