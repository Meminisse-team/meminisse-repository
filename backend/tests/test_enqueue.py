"""Celery 큐잉 재시도 헬퍼(app/workers/enqueue.py) 테스트.

핵심 계약: 브로커 연결 실패를 짧은 재시도로 흡수하고, 그래도 실패하면 예외를
전파하지 않고 False를 반환한다 — 세션 완료 자체는 이미 커밋된 뒤라 큐잉
실패가 사용자 응답을 막으면 안 되기 때문이다.
"""

from __future__ import annotations

import pytest

from app.workers.enqueue import enqueue_with_retry


class _FakeTask:
    """Celery task 흉내 — .delay(*args)가 동기 함수라는 점(asyncio.to_thread로
    감싸 호출됨)까지 실제 Celery task와 동일하게 재현한다."""

    name = "fake_task"

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls: list[tuple] = []

    def delay(self, *args) -> None:
        self.calls.append(args)
        if len(self.calls) <= self.fail_times:
            raise ConnectionError("broker unavailable")


@pytest.mark.asyncio
async def test_succeeds_on_first_attempt_without_retry() -> None:
    task = _FakeTask(fail_times=0)

    result = await enqueue_with_retry(task, "arg1", base_delay_seconds=0)

    assert result is True
    assert len(task.calls) == 1


@pytest.mark.asyncio
async def test_recovers_after_transient_failures() -> None:
    """처음 두 번은 브로커 연결 실패, 세 번째에 성공하는 경우를 재현한다 —
    2026-07-15 사고(Redis 순간 다운) 재현 시나리오."""
    task = _FakeTask(fail_times=2)

    result = await enqueue_with_retry(task, "session-id", attempts=3, base_delay_seconds=0)

    assert result is True
    assert len(task.calls) == 3


@pytest.mark.asyncio
async def test_returns_false_without_raising_when_all_attempts_fail() -> None:
    """브로커가 재시도 예산을 넘어서도 계속 죽어 있으면 예외를 전파하지 않고
    False만 반환한다 — 호출부(interview_service.complete_session)가 사용자
    응답을 막지 않아야 하므로."""
    task = _FakeTask(fail_times=10)

    result = await enqueue_with_retry(task, "session-id", attempts=3, base_delay_seconds=0)

    assert result is False
    assert len(task.calls) == 3
