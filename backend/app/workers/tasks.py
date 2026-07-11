"""
Celery 태스크는 동기 함수여야 하므로, 각 태스크는 asyncio.run()으로 서비스 레이어의
비동기 함수를 감싸 실행한다. FastAPI 요청 경로와는 별개로, 태스크마다
app.gateways.factory.gateways_context()로 독립된 게이트웨이 묶음을 새로 연다
(워커 프로세스는 요청-응답 생명주기가 없다). GATEWAY_BACKEND 설정에 따라 이 컨텍스트가
Mock/Postgres 어느 쪽을 열지 자동으로 결정하므로, 이 파일은 백엔드가 무엇인지 몰라도 된다.

app.database.engine은 프로세스 전역에 하나만 있는 SQLAlchemy 엔진(연결 풀)이다. FastAPI는
이벤트 루프가 프로세스 생명주기 내내 하나로 유지되니 문제가 없지만, 이 워커는 태스크마다
asyncio.run()으로 새 이벤트 루프를 만들고 태스크가 끝나면 그 루프를 닫아버린다. 그 루프
안에서 풀에 반납된 asyncpg 커넥션은 그 (이미 닫힌) 루프에 종속되어 있어서, 다음 태스크가
새 루프에서 같은 풀의 그 커넥션을 재사용하려 하면 "RuntimeError: Event loop is closed"로
죽는다(실제 Redis+워커 연동 검증 중 두 번째 세션의 이벤트 추출 태스크에서 재현, 2026-07-11).
그래서 각 태스크가 끝날 때마다(성공/실패 무관) 같은 루프 안에서 engine.dispose()로 풀을
비워, 다음 태스크가 새 루프에서 커넥션을 처음부터 새로 맺도록 한다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from typing import Any

from app.database import engine
from app.gateways.factory import gateways_context
from app.services import autobiography_service, event_extraction_service
from app.workers.celery_app import celery_app


def _run(coro: Coroutine[Any, Any, None]) -> None:
    async def _wrapped() -> None:
        try:
            await coro
        finally:
            await engine.dispose()

    asyncio.run(_wrapped())


@celery_app.task(name="process_session_completion")
def process_session_completion(session_id: str) -> None:
    _run(_process_session_completion_async(uuid.UUID(session_id)))


async def _process_session_completion_async(session_id: uuid.UUID) -> None:
    async with gateways_context() as gateways:
        await event_extraction_service.process_completed_session(gateways, session_id)


@celery_app.task(name="consolidate_autobiography")
def consolidate_autobiography(user_id: str) -> None:
    """Phase 3(이벤트 병합·중요도 산정·스타일 바이블). 모든 인터뷰 세션 종료 후 트리거."""
    _run(_consolidate_autobiography_async(uuid.UUID(user_id)))


async def _consolidate_autobiography_async(user_id: uuid.UUID) -> None:
    async with gateways_context() as gateways:
        await autobiography_service.consolidate_autobiography(gateways, user_id)


@celery_app.task(name="write_chapter")
def write_chapter(chapter_draft_id: str) -> None:
    """Phase 4 챕터 단위 집필(시놉시스·본문·팩트체크·근거검증·등장인물 스캔)."""
    _run(_write_chapter_async(uuid.UUID(chapter_draft_id)))


async def _write_chapter_async(chapter_draft_id: uuid.UUID) -> None:
    async with gateways_context() as gateways:
        await autobiography_service.write_chapter(gateways, chapter_draft_id)


@celery_app.task(name="finalize_manuscript")
def finalize_manuscript(autobiography_id: str) -> None:
    """Phase 4 통일성 윤문 패스. 모든 챕터 집필 완료 후 트리거."""
    _run(_finalize_manuscript_async(uuid.UUID(autobiography_id)))


async def _finalize_manuscript_async(autobiography_id: uuid.UUID) -> None:
    async with gateways_context() as gateways:
        await autobiography_service.finalize_manuscript(gateways, autobiography_id)
