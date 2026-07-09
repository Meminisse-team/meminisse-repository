"""
Celery 태스크는 동기 함수여야 하므로, 각 태스크는 asyncio.run()으로 서비스 레이어의
비동기 함수를 감싸 실행한다. FastAPI 요청 경로와는 별개로, 태스크마다
app.gateways.factory.gateways_context()로 독립된 게이트웨이 묶음을 새로 연다
(워커 프로세스는 요청-응답 생명주기가 없다). GATEWAY_BACKEND 설정에 따라 이 컨텍스트가
Mock/Postgres 어느 쪽을 열지 자동으로 결정하므로, 이 파일은 백엔드가 무엇인지 몰라도 된다.
"""

from __future__ import annotations

import asyncio
import uuid

from app.gateways.factory import gateways_context
from app.services import event_extraction_service
from app.workers.celery_app import celery_app


@celery_app.task(name="process_session_completion")
def process_session_completion(session_id: str) -> None:
    asyncio.run(_process_session_completion_async(uuid.UUID(session_id)))


async def _process_session_completion_async(session_id: uuid.UUID) -> None:
    async with gateways_context() as gateways:
        await event_extraction_service.process_completed_session(gateways, session_id)
