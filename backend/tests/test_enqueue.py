"""Celery 큐잉 재시도 헬퍼(app/workers/enqueue.py) 테스트.

핵심 계약: 브로커 연결 실패를 짧은 재시도로 흡수하고, 그래도 실패하면 예외를
전파하지 않고 False를 반환한다 — 세션 완료 자체는 이미 커밋된 뒤라 큐잉
실패가 사용자 응답을 막으면 안 되기 때문이다.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

from app.workers import enqueue
from app.workers.enqueue import (
    enqueue_finalize_manuscript,
    enqueue_generate_manuscript_pdf,
    enqueue_session_phase2_processing,
    enqueue_session_phase2_processing_in_background,
    enqueue_with_retry,
    enqueue_write_chapter,
    release_chapter_write_lock,
    release_finalize_lock,
    release_pdf_generate_lock,
)


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


class _FakeRedisClient:
    """test_reconcile_lock.py의 _FakeRedisClient와 동일한 패턴 — 실제 Redis
    없이 SET NX EX / DELETE 결과만 흉내 낸다."""

    def __init__(self, *, set_result: bool) -> None:
        self._set_result = set_result
        self.set_calls: list[tuple] = []
        self.delete_calls: list[str] = []
        self.closed = False

    async def set(self, key, value, *, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        return self._set_result

    async def delete(self, key) -> None:
        self.delete_calls.append(key)

    async def aclose(self) -> None:
        self.closed = True


class _RaisingRedisClient:
    """Redis 연결 자체가 실패하는 상황(브로커 다운) 재현용."""

    async def set(self, key, value, *, nx=False, ex=None):
        raise ConnectionError("broker unavailable")

    async def delete(self, key) -> None:
        raise ConnectionError("broker unavailable")

    async def aclose(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# 세션 단위 phase2 중복 재큐잉 방지 락(2026-07-19)                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_session_phase2_lock_acquired_when_key_not_already_set() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    session_id = uuid.uuid4()
    with patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client):
        acquired = await enqueue._acquire_session_phase2_lock(session_id)

    assert acquired is True
    assert fake_client.set_calls == [
        (f"session_phase2_pending:{session_id}", "1", True, enqueue._SESSION_PHASE2_LOCK_TTL_SECONDS)
    ]
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_session_phase2_lock_denied_when_already_pending() -> None:
    """같은 세션의 처리 메시지가 이미 대기/진행 중이면(락이 걸려 있으면) 락
    획득에 실패해야 한다 — 이게 중복 재큐잉을 막는 핵심 계약."""
    fake_client = _FakeRedisClient(set_result=False)
    with patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client):
        acquired = await enqueue._acquire_session_phase2_lock(uuid.uuid4())

    assert acquired is False


@pytest.mark.asyncio
async def test_session_phase2_lock_fails_open_when_redis_unreachable() -> None:
    """Redis 자체가 응답하지 않으면(연결 실패) 중복 방지보다 유실 방지가
    우선이므로 락 획득 성공으로 간주해야 한다 — 안 그러면 브로커가 잠깐
    흔들리는 순간에 Phase 2 큐잉 자체가 조용히 막혀버린다."""
    with patch("app.workers.enqueue.redis_asyncio.from_url", return_value=_RaisingRedisClient()):
        acquired = await enqueue._acquire_session_phase2_lock(uuid.uuid4())

    assert acquired is True


@pytest.mark.asyncio
async def test_enqueue_session_phase2_skips_when_lock_already_held() -> None:
    """락을 이미 다른 곳이 쥐고 있으면(=이미 대기/진행 중) enqueue_with_retry
    자체를 호출하지 않고 False를 반환해야 한다."""
    fake_client = _FakeRedisClient(set_result=False)
    task = _FakeTask()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.process_session_completion", task),
    ):
        result = await enqueue_session_phase2_processing(uuid.uuid4(), log_context="test")

    assert result is False
    assert task.calls == []


@pytest.mark.asyncio
async def test_enqueue_session_phase2_proceeds_when_lock_acquired() -> None:
    """락을 새로 획득했으면(=처음 큐잉하는 세션) 실제로 큐잉까지 이어져야 한다."""
    fake_client = _FakeRedisClient(set_result=True)
    task = _FakeTask()
    session_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.process_session_completion", task),
    ):
        result = await enqueue_session_phase2_processing(session_id, log_context="test")

    assert result is True
    assert task.calls == [(str(session_id),)]


@pytest.mark.asyncio
async def test_enqueue_session_phase2_in_background_returns_immediately() -> None:
    """HTTP 요청 흐름과 분리해야 하는 호출부용 — 락 확인을 포함해 아무것도
    기다리지 않고 즉시 반환해야 한다(enqueue_in_background와 동일한 계약)."""
    fake_client = _FakeRedisClient(set_result=True)
    task = _FakeTask()
    session_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.process_session_completion", task),
    ):
        result = enqueue_session_phase2_processing_in_background(session_id, log_context="test")
        assert result is None  # 호출 즉시 반환 — 코루틴이 아니라 이미 끝난 동기 호출

        # 백그라운드 태스크는 asyncio.to_thread(실제 스레드 풀)까지 거치므로
        # 고정된 sleep(0) 횟수로는 완료를 보장할 수 없다 — 완료될 때까지 짧게
        # 폴링한다(최대 0.5초).
        for _ in range(50):
            if task.calls:
                break
            await asyncio.sleep(0.01)

    assert task.calls == [(str(session_id),)]


# --------------------------------------------------------------------------- #
# 챕터 단위 write_chapter 중복 재큐잉 방지 락(2026-07-19)                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_enqueue_write_chapter_skips_when_already_pending() -> None:
    """이미 대기/처리 중인 챕터는(락 획득 실패) 다시 큐잉하지 않아야 한다 —
    "각 장 집필 시작"이 본문 없는 챕터 전부를 한 번에 요청할 때, 이미 큐에
    들어간 챕터까지 중복으로 다시 쌓이던 문제의 핵심 계약."""
    fake_client = _FakeRedisClient(set_result=False)
    task = _FakeTask()
    chapter_draft_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.write_chapter", task),
    ):
        result = await enqueue_write_chapter(chapter_draft_id, log_context="test")

    assert result is False
    assert task.calls == []


@pytest.mark.asyncio
async def test_enqueue_write_chapter_proceeds_when_lock_acquired() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    task = _FakeTask()
    chapter_draft_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.write_chapter", task),
    ):
        result = await enqueue_write_chapter(chapter_draft_id, log_context="test")

    assert result is True
    assert task.calls == [(str(chapter_draft_id),)]


@pytest.mark.asyncio
async def test_enqueue_write_chapter_releases_lock_when_delay_fails() -> None:
    """큐잉 자체가 브로커 재시도까지 전부 실패하면, 아무것도 큐에 들어가지
    않았으니 락도 즉시 풀어야 한다 — 안 그러면 진짜 재시도할 방법이 없어진다."""
    fake_client = _FakeRedisClient(set_result=True)
    task = _FakeTask(fail_times=10)
    chapter_draft_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.write_chapter", task),
    ):
        result = await enqueue_write_chapter(chapter_draft_id, log_context="test")

    assert result is False
    assert fake_client.delete_calls == [f"chapter_write_pending:{chapter_draft_id}"]


@pytest.mark.asyncio
async def test_release_chapter_write_lock_deletes_key() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    chapter_draft_id = uuid.uuid4()
    with patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client):
        await release_chapter_write_lock(chapter_draft_id)

    assert fake_client.delete_calls == [f"chapter_write_pending:{chapter_draft_id}"]


# --------------------------------------------------------------------------- #
# finalize_manuscript / generate_manuscript_pdf 중복 재큐잉 방지 락(2026-07-19) #
# --------------------------------------------------------------------------- #
# 실사용 중 큐를 직접 점검하다가 이미 PUBLISHED된 자서전에 대해 finalize_manuscript가
# 중복 실행 중인 것과, generate_manuscript_pdf가 같은 인자로 큐에 2개 쌓여 있는
# 것을 발견했다 — write_chapter와 동일한 락 패턴으로 막는다.


@pytest.mark.asyncio
async def test_enqueue_finalize_manuscript_skips_when_already_pending() -> None:
    fake_client = _FakeRedisClient(set_result=False)
    task = _FakeTask()
    autobiography_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.finalize_manuscript", task),
    ):
        result = await enqueue_finalize_manuscript(autobiography_id, log_context="test")

    assert result is False
    assert task.calls == []


@pytest.mark.asyncio
async def test_enqueue_finalize_manuscript_proceeds_when_lock_acquired() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    task = _FakeTask()
    autobiography_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.finalize_manuscript", task),
    ):
        result = await enqueue_finalize_manuscript(autobiography_id, log_context="test")

    assert result is True
    assert task.calls == [(str(autobiography_id),)]


@pytest.mark.asyncio
async def test_release_finalize_lock_deletes_key() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    autobiography_id = uuid.uuid4()
    with patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client):
        await release_finalize_lock(autobiography_id)

    assert fake_client.delete_calls == [f"finalize_pending:{autobiography_id}"]


@pytest.mark.asyncio
async def test_enqueue_generate_manuscript_pdf_skips_when_already_pending() -> None:
    fake_client = _FakeRedisClient(set_result=False)
    task = _FakeTask()
    autobiography_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.generate_manuscript_pdf", task),
    ):
        result = await enqueue_generate_manuscript_pdf(autobiography_id, log_context="test")

    assert result is False
    assert task.calls == []


@pytest.mark.asyncio
async def test_enqueue_generate_manuscript_pdf_proceeds_when_lock_acquired() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    task = _FakeTask()
    autobiography_id = uuid.uuid4()
    with (
        patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client),
        patch("app.workers.tasks.generate_manuscript_pdf", task),
    ):
        result = await enqueue_generate_manuscript_pdf(autobiography_id, log_context="test")

    assert result is True
    assert task.calls == [(str(autobiography_id),)]


@pytest.mark.asyncio
async def test_release_pdf_generate_lock_deletes_key() -> None:
    fake_client = _FakeRedisClient(set_result=True)
    autobiography_id = uuid.uuid4()
    with patch("app.workers.enqueue.redis_asyncio.from_url", return_value=fake_client):
        await release_pdf_generate_lock(autobiography_id)

    assert fake_client.delete_calls == [f"pdf_generate_pending:{autobiography_id}"]
