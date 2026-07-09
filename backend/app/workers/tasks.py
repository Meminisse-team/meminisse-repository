"""
Celery 태스크는 동기 함수여야 하므로, 각 태스크는 asyncio.run()으로 서비스 레이어의
비동기 함수를 감싸 실행한다. FastAPI 요청 경로의 AsyncSession과는 별개로, 태스크마다
독립된 AsyncSessionLocal 컨텍스트를 새로 연다(워커 프로세스는 요청-응답 생명주기가 없다).
"""

from __future__ import annotations

import asyncio
import uuid

from app.database import AsyncSessionLocal
from app.services import event_extraction_service
from app.workers.celery_app import celery_app


@celery_app.task(name="process_session_completion")
def process_session_completion(session_id: str) -> None:
    asyncio.run(_process_session_completion_async(uuid.UUID(session_id)))


async def _process_session_completion_async(session_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await event_extraction_service.process_completed_session(db, session_id)
