"""관리자 대시보드 라우터. AdminUserDep(app/api/deps.py)이 role=admin만 통과시킨다."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminUserDep, GatewaysDep
from app.schemas.admin import AdminSessionRead
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stale-sessions", response_model=list[AdminSessionRead])
async def list_stale_sessions(
    gateways: GatewaysDep, current_user: AdminUserDep
) -> list[AdminSessionRead]:
    """완료됐지만 Phase 2 후처리(산문 재조립)가 끝나지 않은 채 방치된 세션들 —
    Celery 워커 다운 등으로 처리가 아예 큐잉되지 못한 경우를 발견하기 위함."""
    sessions = await admin_service.list_stale_sessions(gateways, admin_id=current_user.id)
    return [AdminSessionRead.model_validate(s) for s in sessions]


@router.get("/crisis-sessions", response_model=list[AdminSessionRead])
async def list_crisis_sessions(
    gateways: GatewaysDep, current_user: AdminUserDep
) -> list[AdminSessionRead]:
    """위기 대응 문구(TIER2_CRISIS_RESPONSE)가 발화된 세션들 — 안전 책임 소재상
    사람이 사후 검토할 수 있어야 한다."""
    sessions = await admin_service.list_crisis_sessions(gateways, admin_id=current_user.id)
    return [AdminSessionRead.model_validate(s) for s in sessions]
