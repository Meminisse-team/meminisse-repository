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
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.agents import prompts
from app.api.deps import require_admin
from app.gateways.dto import EventCreateData, SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import (
    AutobiographyStatus,
    EventSourceType,
    MessageRole,
    SessionStatus,
    SessionType,
    UserRole,
)
from app.services import admin_service


def _patch_extraction_pipeline(*, summary: str = "새로 추출된 이벤트"):
    """test_story_prose_edit.py와 동일한 패턴 — 관리자 산문 수정도 내부적으로
    event_extraction_service.reextract_events_from_edited_prose를 그대로 재사용
    하므로 같은 파이프라인(Solar 구조화 추출·임베딩)을 모킹해야 한다."""

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        assert schema_name == "event_extraction"
        return {
            "events": [
                {
                    "one_line_summary": summary,
                    "prose_paragraph": "재추출된 문단",
                    "source_quote": "재추출된 문단",
                }
            ],
            "relations": [],
        }

    async def _fake_embed_passages(texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    return (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_passages", new=_fake_embed_passages),
    )


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


@pytest.mark.asyncio
async def test_reconcile_stale_sessions_requeues_only_stale_ones() -> None:
    """2026-07-16 아키텍처 개선: 큐잉 실패(브로커 순간 다운 등)로 방치된 세션을
    관리자가 대시보드를 열어봐야만 발견하던 것을, Celery Beat가 이 함수를
    주기적으로 호출해 사람 개입 없이 스스로 복구한다.

    list_stale_completed는 관리자 전체 조회용이라 user_id로 스코프하지 않는다
    (의도된 설계) — 그래서 이 테스트는 default_store를 공유하는 다른 테스트가
    남긴 stale 세션이 섞여 있어도 안전하도록 정확한 총 개수 대신 "내가 만든
    세션이 포함/제외됐는가"만 확인한다.

    세션 단위 중복 재큐잉 방지 락(app/workers/enqueue.py, 2026-07-19)은
    conftest.py의 자동 패치 덕분에 실제 Redis 없이도 항상 락 획득에 성공한다 —
    이 테스트의 관심사는 "stale 판정이 정확한가"이지 락 동작이 아니다(락 자체는
    test_enqueue.py가 검증)."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)
    now = datetime.now(timezone.utc)
    stale = await _make_stale_completed_session(gateways, user.id, completed_at=now - timedelta(minutes=30))
    recent = await _make_stale_completed_session(gateways, user.id, completed_at=now - timedelta(minutes=1))

    requeued_ids: list[str] = []
    with patch(
        "app.workers.tasks.process_session_completion.delay",
        new=lambda session_id: requeued_ids.append(session_id),
    ):
        await admin_service.reconcile_stale_sessions(gateways)

    assert str(stale.id) in requeued_ids
    assert str(recent.id) not in requeued_ids


async def _make_completed_session_with_prose(gateways, user_id, *, prose: str = "산문"):
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user_id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(session.id, role=MessageRole.ASSISTANT, content="질문 내용")
    await gateways.sessions.set_session_prose(session.id, prose)
    await gateways.sessions.complete(session.id)
    await gateways.commit()
    return session


@pytest.mark.asyncio
async def test_lookup_user_by_id_and_by_email() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)

    by_id = await admin_service.lookup_user(gateways, admin_id=admin.id, identifier=str(target.id))
    by_email = await admin_service.lookup_user(gateways, admin_id=admin.id, identifier=target.email)

    assert by_id is not None and by_id[0].id == target.id
    assert by_email is not None and by_email[0].id == target.id


@pytest.mark.asyncio
async def test_lookup_user_not_found_returns_none() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)

    result = await admin_service.lookup_user(gateways, admin_id=admin.id, identifier="nobody@test.local")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_user_records_audit_log() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)

    await admin_service.lookup_user(gateways, admin_id=admin.id, identifier=str(target.id))

    from app.gateways.mock.store import default_store

    matching = [log for log in default_store.audit_logs if log.admin_id == admin.id]
    assert any(
        log.action == "view_user_detail" and log.target_user_id == target.id for log in matching
    )


@pytest.mark.asyncio
async def test_admin_update_user_session_prose_overwrites_and_reextracts() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    session = await _make_completed_session_with_prose(gateways, target.id, prose="AI가 재조립한 산문.")

    p1, p2 = _patch_extraction_pipeline(summary="관리자 수정 후 재추출된 이벤트")
    with p1, p2:
        updated = await admin_service.update_user_session_prose(
            gateways,
            admin_id=admin.id,
            user_id=target.id,
            session_id=session.id,
            new_prose="관리자가 고친 산문.",
        )

    assert updated.session_prose == "관리자가 고친 산문."
    events = await gateways.events.list_by_session(session.id)
    assert [e.one_line_summary for e in events] == ["관리자 수정 후 재추출된 이벤트"]


@pytest.mark.asyncio
async def test_admin_update_user_session_prose_has_no_cooldown() -> None:
    """일반 유저는 60초 쿨다운(story_service._PROSE_EDIT_COOLDOWN)에 걸리지만,
    관리자는 연속으로 즉시 두 번 고칠 수 있어야 한다."""
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    session = await _make_completed_session_with_prose(gateways, target.id, prose="원본")

    p1, p2 = _patch_extraction_pipeline()
    with p1, p2:
        await admin_service.update_user_session_prose(
            gateways, admin_id=admin.id, user_id=target.id, session_id=session.id, new_prose="1차 수정"
        )
        updated = await admin_service.update_user_session_prose(
            gateways, admin_id=admin.id, user_id=target.id, session_id=session.id, new_prose="2차 수정"
        )

    assert updated.session_prose == "2차 수정"


@pytest.mark.asyncio
async def test_admin_update_user_session_prose_wrong_owner_raises_not_found() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    owner = await _make_user(gateways)
    other = await _make_user(gateways)
    session = await _make_completed_session_with_prose(gateways, owner.id)

    with pytest.raises(admin_service.AdminSessionNotFoundError):
        await admin_service.update_user_session_prose(
            gateways, admin_id=admin.id, user_id=other.id, session_id=session.id, new_prose="침입 시도"
        )


@pytest.mark.asyncio
async def test_admin_update_user_session_prose_before_processing_raises() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    session = await gateways.sessions.create(
        SessionCreateData(user_id=target.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.commit()

    with pytest.raises(admin_service.AdminProseNotReadyError):
        await admin_service.update_user_session_prose(
            gateways, admin_id=admin.id, user_id=target.id, session_id=session.id, new_prose="아직 처리 전"
        )


@pytest.mark.asyncio
async def test_admin_update_user_email_success_calls_supabase_then_local_db() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)

    calls: list[dict] = []

    async def _fake_admin_update_user(user_id, *, email=None, password=None):
        calls.append({"user_id": user_id, "email": email, "password": password})

    with patch("app.services.admin_service.supabase_auth.admin_update_user", new=_fake_admin_update_user):
        updated = await admin_service.update_user_email(
            gateways, admin_id=admin.id, user_id=target.id, new_email="new@test.local"
        )

    assert updated.email == "new@test.local"
    assert calls == [{"user_id": target.id, "email": "new@test.local", "password": None}]
    stored = await gateways.users.get_by_id(target.id)
    assert stored.email == "new@test.local"


@pytest.mark.asyncio
async def test_admin_update_user_email_rejects_duplicate() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    other = await _make_user(gateways)

    with patch("app.services.admin_service.supabase_auth.admin_update_user") as mocked:
        with pytest.raises(admin_service.AdminEmailAlreadyRegisteredError):
            await admin_service.update_user_email(
                gateways, admin_id=admin.id, user_id=target.id, new_email=other.email
            )
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_admin_reset_user_password_calls_supabase_and_does_not_leak_password() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)

    calls: list[dict] = []

    async def _fake_admin_update_user(user_id, *, email=None, password=None):
        calls.append({"user_id": user_id, "email": email, "password": password})

    with patch("app.services.admin_service.supabase_auth.admin_update_user", new=_fake_admin_update_user):
        await admin_service.reset_user_password(
            gateways, admin_id=admin.id, user_id=target.id, new_password="new-secret-pw"
        )

    assert calls == [{"user_id": target.id, "email": None, "password": "new-secret-pw"}]

    from app.gateways.mock.store import default_store

    matching = [log for log in default_store.audit_logs if log.admin_id == admin.id]
    reset_logs = [log for log in matching if log.action == "admin_reset_user_password"]
    assert len(reset_logs) == 1
    assert "new-secret-pw" not in reset_logs[0].action


@pytest.mark.asyncio
async def test_list_db_table_users_and_sessions() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    session = await _make_completed_session_with_prose(gateways, target.id)

    users = await admin_service.list_db_table(gateways, admin_id=admin.id, table="users", limit=50, offset=0)
    sessions = await admin_service.list_db_table(
        gateways, admin_id=admin.id, table="sessions", limit=50, offset=0
    )

    assert target.id in {u.id for u in users}
    assert session.id in {s.id for s in sessions}


@pytest.mark.asyncio
async def test_list_db_table_events_strips_embedding() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    await gateways.events.create(
        EventCreateData(
            user_id=target.id,
            source_type=EventSourceType.SESSION_CHAT,
            one_line_summary="원본 벡터 확인용 이벤트",
            prose_paragraph="문단",
            verified=True,
            embedding=[0.1, 0.2, 0.3],
        )
    )
    await gateways.commit()

    events = await admin_service.list_db_table(
        gateways, admin_id=admin.id, table="events", limit=50, offset=0
    )

    assert all(e.embedding is None for e in events)


@pytest.mark.asyncio
async def test_list_audit_logs_returns_recent_entries() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    await admin_service.list_stale_sessions(gateways, admin_id=admin.id)

    logs = await admin_service.list_audit_logs(gateways, admin_id=admin.id, limit=50, offset=0)

    assert any(log.action == "view_stale_sessions" and log.admin_id == admin.id for log in logs)


async def _make_finished_autobiography(gateways, user_id, *, final_content: str = "완성된 원고"):
    autobiography = await gateways.autobiographies.create(user_id)
    await gateways.autobiographies.update(
        autobiography.id, final_content=final_content, status=AutobiographyStatus.PUBLISHED
    )
    await gateways.commit()
    return await gateways.autobiographies.get_by_id(autobiography.id)


@pytest.mark.asyncio
async def test_list_user_autobiographies_only_returns_finished_ones() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    finished = await _make_finished_autobiography(gateways, target.id)
    unfinished = await gateways.autobiographies.create(target.id)
    await gateways.commit()

    result = await admin_service.list_user_autobiographies(gateways, admin_id=admin.id, user_id=target.id)

    ids = {a.id for a in result}
    assert finished.id in ids
    assert unfinished.id not in ids


@pytest.mark.asyncio
async def test_list_user_autobiographies_records_audit_log() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)

    await admin_service.list_user_autobiographies(gateways, admin_id=admin.id, user_id=target.id)

    from app.gateways.mock.store import default_store

    matching = [log for log in default_store.audit_logs if log.admin_id == admin.id]
    assert any(
        log.action == "view_user_autobiographies" and log.target_user_id == target.id
        for log in matching
    )


@pytest.mark.asyncio
async def test_trigger_autobiography_pdf_queues_task_for_owned_finished_autobiography() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    autobiography = await _make_finished_autobiography(gateways, target.id)

    queued_ids: list[str] = []
    with patch(
        "app.workers.tasks.generate_manuscript_pdf.delay",
        new=lambda autobiography_id: queued_ids.append(autobiography_id),
    ):
        await admin_service.trigger_autobiography_pdf(
            gateways, admin_id=admin.id, user_id=target.id, autobiography_id=autobiography.id
        )

    assert queued_ids == [str(autobiography.id)]


@pytest.mark.asyncio
async def test_trigger_autobiography_pdf_rejects_wrong_owner() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    owner = await _make_user(gateways)
    other = await _make_user(gateways)
    autobiography = await _make_finished_autobiography(gateways, owner.id)

    with pytest.raises(admin_service.AdminAutobiographyNotFoundError):
        await admin_service.trigger_autobiography_pdf(
            gateways, admin_id=admin.id, user_id=other.id, autobiography_id=autobiography.id
        )


@pytest.mark.asyncio
async def test_trigger_autobiography_pdf_rejects_when_not_finalized() -> None:
    gateways = _build_mock_gateways()
    admin = await _make_user(gateways, role=UserRole.ADMIN)
    target = await _make_user(gateways)
    autobiography = await gateways.autobiographies.create(target.id)
    await gateways.commit()

    with pytest.raises(admin_service.AdminPdfNotReadyError):
        await admin_service.trigger_autobiography_pdf(
            gateways, admin_id=admin.id, user_id=target.id, autobiography_id=autobiography.id
        )


def test_get_app_log_lines_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_service, "_LOG_DIR", tmp_path)

    assert admin_service.get_app_log_lines(service="backend", lines=200) == []


def test_get_app_log_lines_returns_last_n_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_service, "_LOG_DIR", tmp_path)
    log_file = tmp_path / "worker.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(10)) + "\n", encoding="utf-8")

    lines = admin_service.get_app_log_lines(service="worker", lines=3)

    assert [line.strip() for line in lines] == ["line 7", "line 8", "line 9"]
