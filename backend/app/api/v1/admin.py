"""관리자 대시보드 라우터. AdminUserDep(app/api/deps.py)이 role=admin만 통과시킨다."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminUserDep, GatewaysDep
from app.clients.supabase_auth import SupabaseAuthError
from app.schemas.admin import (
    AdminAuditLogRead,
    AdminDbTable,
    AdminEmailUpdate,
    AdminLogLinesRead,
    AdminLogService,
    AdminPasswordReset,
    AdminProseUpdate,
    AdminSessionDetailRead,
    AdminSessionRead,
    AdminUserDetail,
)
from app.schemas.autobiography import AutobiographyRead
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])

_DB_TABLE_LIMIT_MAX = 200


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


@router.get("/users/lookup", response_model=AdminUserDetail)
async def lookup_user(
    gateways: GatewaysDep,
    current_user: AdminUserDep,
    identifier: str = Query(..., description="유저 UUID 또는 이메일"),
) -> AdminUserDetail:
    """이메일 또는 UUID로 유저를 찾아 프로필과 세션 전체(산문 포함)를 반환한다."""
    found = await admin_service.lookup_user(gateways, admin_id=current_user.id, identifier=identifier)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "유저를 찾을 수 없습니다.")
    user, sessions = found
    return AdminUserDetail(
        id=user.id,
        email=user.email,
        name=user.name,
        birth_year=user.birth_year,
        hometown=user.hometown,
        current_stage=user.current_stage,
        role=user.role,
        education_level=user.education_level,
        marital_status=user.marital_status,
        has_children=user.has_children,
        sessions=[AdminSessionDetailRead.model_validate(s) for s in sessions],
    )


@router.patch("/users/{user_id}/sessions/{session_id}/prose", response_model=AdminSessionDetailRead)
async def update_user_session_prose(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: AdminProseUpdate,
    gateways: GatewaysDep,
    current_user: AdminUserDep,
) -> AdminSessionDetailRead:
    """관리자가 유저 대신 산문을 고친다 — 쿨다운 없이 즉시 반영, 이벤트도 함께
    재추출된다(app/services/admin_service.py:update_user_session_prose)."""
    try:
        session = await admin_service.update_user_session_prose(
            gateways,
            admin_id=current_user.id,
            user_id=user_id,
            session_id=session_id,
            new_prose=payload.prose,
        )
    except admin_service.AdminSessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다.")
    except admin_service.AdminProseNotReadyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "아직 산문 재조립이 끝나지 않았습니다.")
    return AdminSessionDetailRead.model_validate(session)


@router.patch("/users/{user_id}/email", response_model=AdminUserDetail)
async def update_user_email(
    user_id: uuid.UUID,
    payload: AdminEmailUpdate,
    gateways: GatewaysDep,
    current_user: AdminUserDep,
) -> AdminUserDetail:
    """로그인 이메일을 Supabase Auth와 이 앱의 users 테이블 양쪽에 반영한다."""
    try:
        user = await admin_service.update_user_email(
            gateways, admin_id=current_user.id, user_id=user_id, new_email=payload.new_email
        )
    except admin_service.AdminUserNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "유저를 찾을 수 없습니다.")
    except admin_service.AdminEmailAlreadyRegisteredError:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 등록된 이메일입니다.")
    except SupabaseAuthError as exc:
        raise HTTPException(exc.status_code, str(exc))
    sessions = await gateways.sessions.list_by_user(user_id)
    return AdminUserDetail(
        id=user.id,
        email=user.email,
        name=user.name,
        birth_year=user.birth_year,
        hometown=user.hometown,
        current_stage=user.current_stage,
        role=user.role,
        education_level=user.education_level,
        marital_status=user.marital_status,
        has_children=user.has_children,
        sessions=[AdminSessionDetailRead.model_validate(s) for s in sessions],
    )


@router.post(
    "/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def reset_user_password(
    user_id: uuid.UUID,
    payload: AdminPasswordReset,
    gateways: GatewaysDep,
    current_user: AdminUserDep,
) -> None:
    """비밀번호를 관리자가 직접 지정한다. 값 자체는 응답/감사 로그 어디에도
    남지 않는다."""
    try:
        await admin_service.reset_user_password(
            gateways, admin_id=current_user.id, user_id=user_id, new_password=payload.new_password
        )
    except admin_service.AdminUserNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "유저를 찾을 수 없습니다.")
    except SupabaseAuthError as exc:
        raise HTTPException(exc.status_code, str(exc))


@router.get("/users/{user_id}/autobiographies", response_model=list[AutobiographyRead])
async def list_user_autobiographies(
    user_id: uuid.UUID, gateways: GatewaysDep, current_user: AdminUserDep
) -> list[AutobiographyRead]:
    """고객이 완성한 자서전 목록 — 관리자가 실물 인쇄용 PDF를 내려받거나 아직
    조판 전이면 대신 생성을 트리거하는 화면 전용."""
    autobiographies = await admin_service.list_user_autobiographies(
        gateways, admin_id=current_user.id, user_id=user_id
    )
    return [AutobiographyRead.model_validate(a) for a in autobiographies]


@router.post(
    "/users/{user_id}/autobiographies/{autobiography_id}/pdf/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def admin_generate_autobiography_pdf(
    user_id: uuid.UUID,
    autobiography_id: uuid.UUID,
    gateways: GatewaysDep,
    current_user: AdminUserDep,
) -> dict:
    """실물 출판 준비를 위해 관리자가 고객 대신 PDF 조판을 큐잉한다."""
    try:
        await admin_service.trigger_autobiography_pdf(
            gateways, admin_id=current_user.id, user_id=user_id, autobiography_id=autobiography_id
        )
    except admin_service.AdminAutobiographyNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "자서전을 찾을 수 없습니다.")
    except admin_service.AdminPdfNotReadyError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "최종본(윤문)이 완성된 뒤에 PDF를 만들 수 있습니다."
        )
    return {"detail": "Manuscript PDF generation queued"}


@router.get("/db/{table}")
async def list_db_table(
    table: AdminDbTable,
    gateways: GatewaysDep,
    current_user: AdminUserDep,
    limit: int = Query(50, ge=1, le=_DB_TABLE_LIMIT_MAX),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """구조화된 읽기 전용 DB 열람 — 화이트리스트된 5개 도메인 테이블만 조회
    가능하다(임의 SQL 없음). 각 레코드는 dataclass라 응답 모델을 테이블마다
    따로 두는 대신 dict로 직렬화한다(내부 관리자 도구라 API 계약을 엄격히
    고정할 필요가 없다)."""
    rows = await admin_service.list_db_table(
        gateways, admin_id=current_user.id, table=table.value, limit=limit, offset=offset
    )
    return [_row_to_dict(row) for row in rows]


@router.get("/audit-logs", response_model=list[AdminAuditLogRead])
async def list_audit_logs(
    gateways: GatewaysDep,
    current_user: AdminUserDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[AdminAuditLogRead]:
    logs = await admin_service.list_audit_logs(
        gateways, admin_id=current_user.id, limit=limit, offset=offset
    )
    return [AdminAuditLogRead.model_validate(log) for log in logs]


@router.get("/logs", response_model=AdminLogLinesRead)
async def get_app_logs(
    _current_user: AdminUserDep,
    service: AdminLogService = Query(...),
    lines: int = Query(200, ge=1, le=2000),
) -> AdminLogLinesRead:
    """백엔드/워커/beat 프로세스의 최근 로그 라인. 파일이 아직 없으면(로그
    미기록) 빈 배열을 반환한다 — 에러가 아니다."""
    return AdminLogLinesRead(lines=admin_service.get_app_log_lines(service=service.value, lines=lines))


def _row_to_dict(row: object) -> dict:
    return {k: v for k, v in vars(row).items()}
