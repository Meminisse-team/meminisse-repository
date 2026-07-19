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
import uuid
from typing import Any

import redis.asyncio as redis_asyncio

from app.config import settings

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


# process_session_completion 중복 재큐잉 방지(2026-07-19) — reconcile_stale_sessions
# (admin_service.py)는 "COMPLETED인데 session_prose가 아직 없고 10분 넘게 지남"이라는
# 조건 하나로만 재큐잉 대상을 고른다. 이 조건은 "메시지가 진짜 유실됨"과 "이미 큐에
# 들어가 순서를 기다리는 중"을 구분하지 못한다 — 워커가 챕터 집필 등으로 오래 바빠
# 대기열이 10분 넘게 밀리면, 5분마다 도는 reconcile이 같은 세션을 무한정 다시
# 큐잉해 대기열이 눈덩이처럼 불어난다(2026-07-18 실사용 중 재현: 세션 후처리
# 메시지가 378개까지 중복 누적). Redis SET NX EX로 세션 단위 락을 걸어, 이미
# 대기/처리 중인 세션은 재큐잉을 건너뛴다 — tasks.py의 _acquire_reconcile_lock과
# 동일한 패턴을 세션 단위로 적용한 것이다.
_SESSION_PHASE2_LOCK_PREFIX = "session_phase2_pending:"
# 정상 트리거(complete_session)든 안전망(reconcile)이든 이 TTL 안에는 재큐잉하지
# 않는다 — 값은 "이 세션의 처리 메시지가 큐에서 시작되기까지 현실적으로 걸릴 수
# 있는 최악의 대기 시간"보다 넉넉해야 한다. 너무 짧으면 원래 버그가 완화될 뿐
# 재발하고, 너무 길면 진짜로 유실된 경우의 복구가 그만큼 늦어진다 — 1시간은 지금
# 관찰된 최악의 백로그(챕터 집필 수십 건이 앞에 밀려 있는 경우)도 대부분 커버하면서,
# 진짜 유실 시 복구 지연도 감내할 만한 수준이라 골랐다.
_SESSION_PHASE2_LOCK_TTL_SECONDS = 3600


async def _acquire_session_phase2_lock(session_id: uuid.UUID | str) -> bool:
    """Redis `SET NX EX`(원자적 락 획득). 이미 걸려 있으면 False — 브로커(Redis)를
    그대로 재사용해 별도 락 저장소를 새로 두지 않는다(tasks.py:_acquire_reconcile_lock
    과 동일한 패턴). Redis 자체가 응답하지 않으면(연결 실패 등) 중복 방지보다
    유실 방지가 우선이므로 "락 획득 성공"으로 간주해 그대로 큐잉을 진행한다 —
    이 폴백이 없으면 브로커가 잠깐 흔들리는 순간에 Phase 2 큐잉 자체가 조용히
    막혀버려, 이 락 기능이 오히려 모듈 docstring이 막으려던 유실 사고를
    재현하게 된다."""
    lock_key = f"{_SESSION_PHASE2_LOCK_PREFIX}{session_id}"
    client = redis_asyncio.from_url(settings.CELERY_BROKER_URL)
    try:
        acquired = await client.set(lock_key, "1", nx=True, ex=_SESSION_PHASE2_LOCK_TTL_SECONDS)
        return bool(acquired)
    except Exception:
        logger.warning(
            "세션 %s의 phase2 락 획득 중 Redis 오류 — 락 없이 큐잉을 진행한다.",
            session_id,
            exc_info=True,
        )
        return True
    finally:
        await client.aclose()


async def enqueue_session_phase2_processing(
    session_id: uuid.UUID | str, *, log_context: str = ""
) -> bool:
    """`process_session_completion` 큐잉 — 호출부가 결과를 기다려도 되는 경우용
    (admin_service.reconcile_stale_sessions처럼 이미 Celery 태스크 컨텍스트 안이라
    블로킹이 문제되지 않는 호출부). HTTP 요청 흐름에서는 대신
    enqueue_session_phase2_processing_in_background를 쓸 것.

    세션 단위 락(모듈 상단 주석 참조)이 이미 걸려 있으면 큐잉 자체를 건너뛰고
    False를 반환한다 — 이미 같은 세션의 처리 메시지가 대기/진행 중이라는 뜻이므로
    중복 큐잉은 낭비일 뿐이다."""
    from app.workers.tasks import process_session_completion  # 순환 임포트 방지용 지연 임포트

    if not await _acquire_session_phase2_lock(session_id):
        logger.info(
            "세션 %s의 Phase 2 처리가 이미 대기/진행 중이라 재큐잉을 건너뜀 (%s)",
            session_id,
            log_context,
        )
        return False
    return await enqueue_with_retry(process_session_completion, str(session_id), log_context=log_context)


def enqueue_session_phase2_processing_in_background(
    session_id: uuid.UUID | str, *, log_context: str = ""
) -> None:
    """`process_session_completion` 큐잉 — interview_service.complete_session/
    add_user_turn(위기 경로)처럼 HTTP 요청 흐름과 완전히 분리해야 하는 호출부용.
    락 확인까지 포함해 전부 백그라운드에서 수행하고 호출 즉시 반환한다
    (enqueue_in_background와 동일한 이유로 — 브로커가 느릴 때도 응답 지연을
    만들지 않기 위함, 모듈 docstring 참조). 세션 단위 중복 큐잉 방지 락은
    enqueue_session_phase2_processing 참조."""
    bg_task = asyncio.create_task(
        enqueue_session_phase2_processing(session_id, log_context=log_context)
    )
    _background_tasks.add(bg_task)
    bg_task.add_done_callback(_background_tasks.discard)


# write_chapter 중복 재큐잉 방지(2026-07-19) — "각 장 집필 시작"이 본문이 없는
# 챕터 전부를 Promise.all로 한 번에 큐잉하는데, write_chapter는 재시도 로직이
# 전혀 없고 Celery도 기본값(task_acks_late=False, 받는 즉시 확인 처리)이라 한
# 번 실패한 챕터는 사람이 다시 트리거하지 않는 한 영원히 집필되지 않는다 —
# 그런데 그 "다시 트리거"가 이미 대기/처리 중인 나머지 챕터까지 통째로 다시
# 큐잉해버려 중복이 쌓인다(2026-07-19 실사용 중 확인: 14개 중 1개만 실패했는데
# 재시도하려면 나머지 12개까지 다시 큐잉될 뻔함). session_phase2 락과 동일한
# 패턴이되, 챕터 "다시 쓰기"는 정상적으로 즉시 재트리거할 수 있어야 하는 기존
# 기능이라(세션 처리와 달리) TTL만 믿지 않고 태스크 종료 시 명시적으로도
# 해제한다(app/workers/tasks.py:write_chapter). TTL은 그 명시적 해제가 실행되지
# 못한 경우(워커 프로세스 자체가 죽는 등)에 대비한 안전망이다.
_CHAPTER_WRITE_LOCK_PREFIX = "chapter_write_pending:"
_CHAPTER_WRITE_LOCK_TTL_SECONDS = 3600


def _chapter_write_lock_key(chapter_draft_id: uuid.UUID | str) -> str:
    return f"{_CHAPTER_WRITE_LOCK_PREFIX}{chapter_draft_id}"


async def _acquire_chapter_write_lock(chapter_draft_id: uuid.UUID | str) -> bool:
    """Redis `SET NX EX` — _acquire_session_phase2_lock과 동일한 패턴(원자적
    획득, Redis 오류 시 fail-open)."""
    lock_key = _chapter_write_lock_key(chapter_draft_id)
    client = redis_asyncio.from_url(settings.CELERY_BROKER_URL)
    try:
        acquired = await client.set(lock_key, "1", nx=True, ex=_CHAPTER_WRITE_LOCK_TTL_SECONDS)
        return bool(acquired)
    except Exception:
        logger.warning(
            "챕터 %s의 집필 락 획득 중 Redis 오류 — 락 없이 큐잉을 진행한다.",
            chapter_draft_id,
            exc_info=True,
        )
        return True
    finally:
        await client.aclose()


async def release_chapter_write_lock(chapter_draft_id: uuid.UUID | str) -> None:
    """write_chapter 태스크가 종료될 때(성공 또는 재시도 소진) 호출한다 —
    사용자가 곧바로 "다시 쓰기"를 눌러도 락에 막히지 않도록. Redis 오류는
    조용히 넘어간다(TTL이 결국 만료되므로 안전망은 남아 있다)."""
    client = redis_asyncio.from_url(settings.CELERY_BROKER_URL)
    try:
        await client.delete(_chapter_write_lock_key(chapter_draft_id))
    except Exception:
        logger.warning("챕터 %s의 집필 락 해제 중 Redis 오류", chapter_draft_id, exc_info=True)
    finally:
        await client.aclose()


async def enqueue_write_chapter(chapter_draft_id: uuid.UUID | str, *, log_context: str = "") -> bool:
    """`write_chapter` 큐잉 — 이미 같은 챕터의 집필이 대기/처리 중이면(락 획득
    실패) 건너뛰고 False를 반환한다. 큐잉 자체가 실패하면(브로커 재시도까지
    전부 실패) 락을 즉시 해제한다 — 아무것도 큐에 들어가지 않았는데 락만 남아
    이후 정상 재시도까지 막아버리면 안 되기 때문이다."""
    from app.workers.tasks import write_chapter as write_chapter_task

    if not await _acquire_chapter_write_lock(chapter_draft_id):
        logger.info(
            "챕터 %s의 집필이 이미 대기/진행 중이라 재큐잉을 건너뜀 (%s)",
            chapter_draft_id,
            log_context,
        )
        return False

    enqueued = await enqueue_with_retry(
        write_chapter_task, str(chapter_draft_id), log_context=log_context
    )
    if not enqueued:
        await release_chapter_write_lock(chapter_draft_id)
    return enqueued


# finalize_manuscript/generate_manuscript_pdf 중복 재큐잉 방지(2026-07-19) — 실사용
# 중 큐를 직접 점검하다가 이미 PUBLISHED(final_content 완성)된 자서전에 대해
# finalize_manuscript가 다시 실행되고 있는 것을 발견했다(해당 엔드포인트가 "이미
# 완성됐는지"를 확인하지 않아 재요청을 그대로 큐잉함). generate_manuscript_pdf도
# 같은 인자로 큐에 2개가 들어가 있었다. write_chapter/session_phase2와 같은
# SET NX EX 락 패턴이지만, 이번엔 락이 두 개(finalize/pdf)뿐이라 공용 헬퍼로 묶어
# 반복을 줄인다 — 기존 session_phase2/chapter_write 락은 이미 검증된 코드라
# 손대지 않고 그대로 둔다.
async def _acquire_pending_lock(key: str, *, ttl_seconds: int) -> bool:
    """SET NX EX(원자적 락 획득). Redis 오류 시 fail-open(락 없이 진행) —
    이 모듈의 다른 락들과 동일한 원칙(유실 방지가 중복 방지보다 우선)."""
    client = redis_asyncio.from_url(settings.CELERY_BROKER_URL)
    try:
        acquired = await client.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(acquired)
    except Exception:
        logger.warning("락 획득 중 Redis 오류(%s) — 락 없이 진행한다.", key, exc_info=True)
        return True
    finally:
        await client.aclose()


async def _release_pending_lock(key: str) -> None:
    client = redis_asyncio.from_url(settings.CELERY_BROKER_URL)
    try:
        await client.delete(key)
    except Exception:
        logger.warning("락 해제 중 Redis 오류(%s)", key, exc_info=True)
    finally:
        await client.aclose()


_FINALIZE_LOCK_PREFIX = "finalize_pending:"
_FINALIZE_LOCK_TTL_SECONDS = 3600


async def release_finalize_lock(autobiography_id: uuid.UUID | str) -> None:
    """finalize_manuscript 태스크가 종료될 때(성공/실패 무관) 호출한다."""
    await _release_pending_lock(f"{_FINALIZE_LOCK_PREFIX}{autobiography_id}")


async def enqueue_finalize_manuscript(
    autobiography_id: uuid.UUID | str, *, log_context: str = ""
) -> bool:
    """`finalize_manuscript` 큐잉 — 이미 같은 자서전의 최종 윤문이 대기/진행
    중이면 건너뛴다. API 엔드포인트가 "이미 final_content가 있으면 409"로
    한 번 더 막지만, 그 확인과 큐잉 사이의 경합(거의 동시에 두 번 클릭 등)은
    이 락이 마지막으로 막는다."""
    from app.workers.tasks import finalize_manuscript as finalize_task

    lock_key = f"{_FINALIZE_LOCK_PREFIX}{autobiography_id}"
    if not await _acquire_pending_lock(lock_key, ttl_seconds=_FINALIZE_LOCK_TTL_SECONDS):
        logger.info(
            "자서전 %s의 최종 윤문이 이미 대기/진행 중이라 재큐잉을 건너뜀 (%s)",
            autobiography_id,
            log_context,
        )
        return False

    enqueued = await enqueue_with_retry(finalize_task, str(autobiography_id), log_context=log_context)
    if not enqueued:
        await release_finalize_lock(autobiography_id)
    return enqueued


_PDF_GENERATE_LOCK_PREFIX = "pdf_generate_pending:"
_PDF_GENERATE_LOCK_TTL_SECONDS = 900


async def release_pdf_generate_lock(autobiography_id: uuid.UUID | str) -> None:
    """generate_manuscript_pdf 태스크가 종료될 때(성공/실패 무관) 호출한다."""
    await _release_pending_lock(f"{_PDF_GENERATE_LOCK_PREFIX}{autobiography_id}")


async def enqueue_generate_manuscript_pdf(
    autobiography_id: uuid.UUID | str, *, log_context: str = ""
) -> bool:
    """`generate_manuscript_pdf` 큐잉 — 이미 같은 자서전의 PDF 조판이 대기/진행
    중이면 건너뛴다. finalize와 달리 "이미 pdf_url이 있는지"는 여기서 막지
    않는다 — 챕터를 고친 뒤 PDF를 다시 만드는 것은 정상적인 재사용 시나리오라
    (실제로 이번 세션에서도 조판 템플릿을 고친 뒤 재생성했다) 완료 여부가 아니라
    "지금 동시에 중복 요청됐는지"만 막는다."""
    from app.workers.tasks import generate_manuscript_pdf as pdf_task

    lock_key = f"{_PDF_GENERATE_LOCK_PREFIX}{autobiography_id}"
    if not await _acquire_pending_lock(lock_key, ttl_seconds=_PDF_GENERATE_LOCK_TTL_SECONDS):
        logger.info(
            "자서전 %s의 PDF 조판이 이미 대기/진행 중이라 재큐잉을 건너뜀 (%s)",
            autobiography_id,
            log_context,
        )
        return False

    enqueued = await enqueue_with_retry(pdf_task, str(autobiography_id), log_context=log_context)
    if not enqueued:
        await release_pdf_generate_lock(autobiography_id)
    return enqueued
