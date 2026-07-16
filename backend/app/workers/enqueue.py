"""
Celery `.delay()` 큐잉을 재시도로 감싸는 공용 헬퍼.

기존에는 `.delay()`가 실패하면(브로커 순간 다운 등) 그냥 로그만 남기고 끝이었다 —
세션 상태(예: InterviewSession.status=COMPLETED)는 이미 커밋됐는데 그 세션의
후처리(session_prose 재조립 등)를 예약하는 메시지 자체가 Redis에 발행되지 못해,
아무 흔적도 없이 영구히 유실되는 사고가 실제로 반복됐다(2026-07-15, 세션 6개가
Redis 다운 동안 완료되어 "나의 이야기"에 영영 안 뜸). Redis가 잠깐(몇 초) 응답하지
않는 흔한 경우까지 매번 사람이 수동으로 재큐잉하지 않도록, 짧은 재시도를 1차
방어선으로 둔다 — 그래도 실패하면(브로커가 더 오래 다운돼 있던 경우) 2차 방어선인
주기적 재조정 태스크(app/services/admin_service.py:reconcile_stale_sessions,
Celery Beat로 5분마다 실행)가 완료됐지만 후처리가 안 끝난 세션을 찾아 다시
큐잉한다 — 두 방어선이 함께 있어야 "브로커가 몇 초 흔들린 경우"와 "브로커가 한동안
아예 죽어 있던 경우"를 모두 사람 개입 없이 커버한다.

`enqueue_with_retry`를 호출부(interview_service.complete_session,
media_service.upload_media_asset)에서 그냥 `await`하면, 재시도 백오프(최대
attempts-1회 sleep)가 고스란히 HTTP 응답을 기다리게 만든다 — 세션 완료 자체는
이미 커밋된 뒤라 큐잉 성공 여부가 사용자 응답에 영향을 줄 이유가 없는데도,
브로커가 잠깐 흔들리는 순간에 딱 걸리면 사용자는 그 대기 시간만큼 "Meminisse가
생각하고 있어요" 로딩을 그대로 보게 된다(2026-07-16, "세션 종료 시 긴 로딩"
문제의 실제 원인 중 하나로 확인됨). 그래서 `enqueue_in_background`로 큐잉을
요청/응답 흐름에서 완전히 분리한다 — 실패해도 무해한 이유(재시도 다 소진해도
2차 방어선이 있음)는 위와 동일하다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ATTEMPTS = 3
_DEFAULT_BASE_DELAY_SECONDS = 1.0


async def enqueue_with_retry(
    task: Any,
    *args: Any,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
    log_context: str = "",
) -> bool:
    """`task.delay(*args)`를 브로커 연결 실패에 한해 지수 백오프로 재시도한다.

    `.delay()` 자체가 브로커에 동기적으로 연결을 시도하는 블로킹 호출이라
    `asyncio.to_thread`로 이벤트 루프 밖에서 돌린다(FastAPI 요청 경로 안에서
    호출될 때, 브로커가 죽어있는 동안 이 프로세스의 다른 모든 동시 요청이 함께
    멎는 걸 막기 위함 — interview_service.complete_session에서 실제 재현된
    문제, 2026-07-11). 모든 시도가 실패하면 False를 반환하고 경고 로그를
    남긴다 — 그래도 영구 유실되지 않는 이유는 주기적 재조정 태스크가 별도로
    있기 때문이다(모듈 docstring 참조)."""
    for attempt in range(1, attempts + 1):
        try:
            await asyncio.to_thread(task.delay, *args)
            return True
        except Exception:
            if attempt == attempts:
                logger.warning(
                    "%s 큐잉 실패 (%s, %d회 재시도 모두 실패) — 주기적 재조정 "
                    "태스크가 나중에 다시 시도한다.",
                    getattr(task, "name", task),
                    log_context,
                    attempts,
                    exc_info=True,
                )
                return False
            await asyncio.sleep(base_delay_seconds * attempt)
    return False


# GC가 실행 중인 태스크를 수거해버리지 않도록 강한 참조를 붙잡아 둔다(asyncio
# 공식 문서 권고 — create_task로 만든 태스크를 아무 변수에도 담지 않으면 다음
# 가비지 컬렉션 주기에 스케줄된 채로 사라질 수 있다).
_background_tasks: set[asyncio.Task[bool]] = set()


def enqueue_in_background(
    task: Any,
    *args: Any,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
    log_context: str = "",
) -> None:
    """`enqueue_with_retry`를 현재 요청/응답 흐름과 분리해 실행한다 — 호출 즉시
    반환하며, 큐잉의 성공/재시도/최종 실패 여부를 기다리지 않는다. 언제 이걸
    쓰는지는 모듈 docstring 참조."""
    bg_task = asyncio.create_task(
        enqueue_with_retry(
            task, *args, attempts=attempts, base_delay_seconds=base_delay_seconds, log_context=log_context
        )
    )
    _background_tasks.add(bg_task)
    bg_task.add_done_callback(_background_tasks.discard)
