"""
사건(Event) 조회 전용 라우터 — '나의 이야기' 탭. 사건을 쓰는 경로(생성·병합·중요도
산정 등)는 전부 내부 파이프라인(interview 세션 종료 후처리, Phase 3 consolidate)이
전담하며 사용자가 직접 호출하는 API가 아니므로, 이 라우터는 조회 하나만 제공한다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep, GatewaysDep
from app.schemas.event import EventRead
from app.services import event_service

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventRead])
async def list_events(gateways: GatewaysDep, current_user: CurrentUserDep) -> list[EventRead]:
    """본인의 검증된(verified=true) 사건을 최근 대화순으로 반환한다(나의 이야기 탭).
    OCR 오인식 의심으로 격리됐거나 아직 확인 질문을 거치지 않은 사건(verified=false)은
    포함하지 않는다."""
    events = await event_service.list_events(gateways, current_user.id)
    return [EventRead.model_validate(event) for event in events]
