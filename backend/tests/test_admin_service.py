"""관리자 대시보드(admin_service.py) 테스트.

2026-07-15 실사용 중 Docker/Celery 워커가 다운된 동안 완료된 세션 6개가 처리
대기 상태로 20분 넘게 방치된 사고를 계기로 도입 — 핵심 계약은 (1) 처리 지연
판정이 임계값을 정확히 지키는지, (2) 위기 대응 로그가 정확히 그 문구가 발화된
세션만 찾는지, (3) 조회할 때마다 감사 로그가 남는지, (4) require_admin이
role=admin이 아니면 거부하는지 네 가지다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.agents import prompts
from app.api.deps import require_admin
from app.gateways.dto import SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import MessageRole, SessionStatus, SessionType, UserRole
from app.services import admin_service


async def _make_user(gateways, *, role: UserRole = UserRole.USER):
    user = await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )
    # MockUserGateway.create는 role=USER로 고정 생성한다 — 관리자 승격은 직접 조작.
    stored = await gateways.users.get_by_id(user.id)
    stored.role = role
    return stored


async def _make_stale_completed_session(gateways, user_id, *, completed_at):
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user_id, session_type=SessionType.FIXED_QUESTION)
    )
    stored = await gateways.sessions.get_by_id(session.id)
    stored.status = SessionStatus.COMPLETED
    stored.completed_at = completed_at
    # session_prose는 의도적으로 비워둔다 — "처리 지연"의 정의 그 자체.
    await gateways.commit()
    return session


@pytest.mark.asyncio
async def test_list_stale_sessions_only_returns_completed_sessions_past_threshold() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    now = datetime.now(timezone.utc)

    stale = await _make_stale_completed_session(gateways, admin.id, completed_at=now - timedelta(minutes=30))
    recent = await _make_stale_completed_session(gateways, admin.id, completed_at=now - timedelta(minutes=1))

    sessions = await admin_service.list_stale_sessions(gateways, admin_id=admin.id)

    ids = {s.id for s in sessions}
    assert stale.id in ids
    assert recent.id not in ids


@pytest.mark.asyncio
async def test_list_stale_sessions_records_audit_log() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)

    await admin_service.list_stale_sessions(gateways, admin_id=admin.id)

    from app.gateways.mock.store import default_store

    matching = [log for log in default_store.audit_logs if log.admin_id == admin.id]
    assert any(log.action == "view_stale_sessions" for log in matching)


@pytest.mark.asyncio
async def test_list_crisis_sessions_matches_exact_crisis_response_only() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    user = await _make_user(gateways)

    crisis_session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        crisis_session.id, role=MessageRole.ASSISTANT, content=prompts.TIER2_CRISIS_RESPONSE
    )
    normal_session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        normal_session.id, role=MessageRole.ASSISTANT, content="평범한 질문입니다."
    )
    await gateways.commit()

    sessions = await admin_service.list_crisis_sessions(gateways, admin_id=admin.id)

    ids = {s.id for s in sessions}
    assert crisis_session.id in ids
    assert normal_session.id not in ids


@pytest.mark.asyncio
async def test_require_admin_rejects_non_admin_user() -> None:
    gateways = _build_mock_gateways()
    regular_user = await _make_user(gateways, role=UserRole.USER)

    with pytest.raises(HTTPException) as exc_info:
        await require_admin(regular_user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_passes_admin_user_through() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)

    result = await require_admin(admin)
    assert result is admin
