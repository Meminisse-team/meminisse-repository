"""
자서전 다중 버전 지원(migration 015, user_id UNIQUE 제약 제거) + 완료된 세션
50개 미만이면 "자서전 집필"을 시작할 수 없는 게이트(2026-07-17 제품 결정) 회귀
테스트.

라우터 레벨 게이트(POST /{user_id}/consolidate)는 FastAPI TestClient로,
서비스 레벨(get_or_create_autobiography가 완성 후 새 버전을 자동 시작하는지,
list_finished_autobiographies가 완성분만 걸러내는지)은 mock 게이트웨이를 직접
써서 검증한다 — 패턴은 tests/test_list_endpoints.py를 따른다.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import patch

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.clients import supabase_auth
from app.config import settings
from app.gateways.dto import SessionCreateData
from app.gateways.factory import _build_mock_gateways
from app.gateways.mock.gateways import MockInterviewSessionGateway
from app.gateways.mock.store import default_store
from app.main import app
from app.models.enums import MessageRole, SessionType
from app.schemas.user import UserCreate
from app.services import autobiography_service, user_service

_TEST_JWT_SECRET = "test-only-secret-do-not-use-elsewhere"


class _FakeSupabaseAuth:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = {}

    def _issue_session(self, email: str) -> dict[str, Any]:
        user_id = self.accounts[email]["id"]
        now = int(time.time())
        access_token = pyjwt.encode(
            {"sub": str(user_id), "aud": "authenticated", "email": email, "iat": now, "exp": now + 3600},
            _TEST_JWT_SECRET,
            algorithm="HS256",
        )
        return {
            "access_token": access_token,
            "refresh_token": f"rt-{uuid.uuid4()}",
            "expires_in": 3600,
            "token_type": "bearer",
        }

    async def admin_create_user(self, *, email: str, password: str, user_metadata: dict) -> uuid.UUID:
        user_id = uuid.uuid4()
        self.accounts[email] = {"id": user_id, "password": password}
        return user_id

    async def sign_in_with_password(self, *, email: str, password: str) -> dict[str, Any]:
        return self._issue_session(email)


@pytest.fixture(autouse=True)
def _reset_mock_store():
    default_store.users.clear()
    default_store.sessions.clear()
    default_store.events.clear()
    default_store.event_relations.clear()
    default_store.media_assets.clear()
    default_store.autobiographies.clear()
    default_store.chapter_drafts.clear()
    default_store.characters.clear()
    default_store.character_mentions.clear()
    default_store.consents.clear()
    default_store.objects.clear()
    yield


@pytest.fixture(autouse=True)
def _patch_supabase_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://test.supabase.co")
    fake = _FakeSupabaseAuth()
    monkeypatch.setattr(supabase_auth, "admin_create_user", fake.admin_create_user)
    monkeypatch.setattr(supabase_auth, "sign_in_with_password", fake.sign_in_with_password)
    yield fake


@pytest.fixture
def client():
    return TestClient(app)


def _signup_and_login(client: TestClient, email: str) -> tuple[str, str]:
    user = client.post(
        "/api/v1/users", json={"email": email, "name": email.split("@")[0], "password": "password123"}
    ).json()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"}).json()[
        "access_token"
    ]
    return user["id"], token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_completed_sessions(user_id: uuid.UUID, count: int) -> None:
    gw = MockInterviewSessionGateway(default_store)
    for i in range(count):
        session = await gw.create(SessionCreateData(user_id=user_id, session_type=SessionType.FIXED_QUESTION))
        await gw.add_chat_log(session.id, role=MessageRole.ASSISTANT, content=f"질문 {i}")
        await gw.set_session_prose(session.id, f"산문 {i}")
        await gw.complete(session.id)


# --------------------------------------------------------------------------- #
# POST /{user_id}/consolidate — 50개 미만이면 409, 이상이면 202               #
# --------------------------------------------------------------------------- #


def test_consolidate_rejected_when_fewer_than_50_completed_sessions(client: TestClient) -> None:
    user_id, token = _signup_and_login(client, "few@example.com")
    import asyncio

    asyncio.run(_seed_completed_sessions(uuid.UUID(user_id), 49))

    with patch("app.workers.tasks.consolidate_autobiography.delay") as delay_mock:
        resp = client.post(f"/api/v1/autobiographies/{user_id}/consolidate", headers=_auth_headers(token))

    assert resp.status_code == 409
    delay_mock.assert_not_called()


def test_consolidate_allowed_when_50_or_more_completed_sessions(client: TestClient) -> None:
    user_id, token = _signup_and_login(client, "enough@example.com")
    import asyncio

    asyncio.run(_seed_completed_sessions(uuid.UUID(user_id), 50))

    with patch("app.workers.tasks.consolidate_autobiography.delay") as delay_mock:
        resp = client.post(f"/api/v1/autobiographies/{user_id}/consolidate", headers=_auth_headers(token))

    assert resp.status_code == 202
    delay_mock.assert_called_once()


def test_get_autobiography_reports_completed_session_count(client: TestClient) -> None:
    user_id, token = _signup_and_login(client, "count@example.com")
    import asyncio

    asyncio.run(_seed_completed_sessions(uuid.UUID(user_id), 3))

    resp = client.get(f"/api/v1/autobiographies/{user_id}", headers=_auth_headers(token))

    assert resp.status_code == 200
    assert resp.json()["completed_session_count"] == 3


# --------------------------------------------------------------------------- #
# 다중 버전 — get_or_create_autobiography가 완성 후 새 버전을 자동 시작하는지 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_or_create_autobiography_reuses_unfinished_version() -> None:
    gateways = _build_mock_gateways()
    user = await user_service.create_user(
        gateways, UserCreate(email="version@example.com", name="테스터", password="test-password-123")
    )

    first = await autobiography_service.get_or_create_autobiography(gateways, user.id)
    second = await autobiography_service.get_or_create_autobiography(gateways, user.id)

    assert first.id == second.id  # 미완성인 동안은 같은 자서전을 계속 돌려줘야 한다.


@pytest.mark.asyncio
async def test_get_or_create_autobiography_starts_new_version_after_previous_finished() -> None:
    gateways = _build_mock_gateways()
    user = await user_service.create_user(
        gateways, UserCreate(email="newversion@example.com", name="테스터", password="test-password-123")
    )

    first = await autobiography_service.get_or_create_autobiography(gateways, user.id)
    await gateways.autobiographies.update(first.id, final_content="완성된 원고")
    await gateways.commit()

    second = await autobiography_service.get_or_create_autobiography(gateways, user.id)

    assert second.id != first.id  # 이전 버전이 완성됐으니 새 버전이 시작돼야 한다.


@pytest.mark.asyncio
async def test_list_finished_autobiographies_only_returns_finished_ones() -> None:
    gateways = _build_mock_gateways()
    user = await user_service.create_user(
        gateways, UserCreate(email="shelf@example.com", name="테스터", password="test-password-123")
    )

    unfinished = await autobiography_service.get_or_create_autobiography(gateways, user.id)
    finished = await gateways.autobiographies.create(user.id)
    await gateways.autobiographies.update(finished.id, final_content="완성된 원고", title="첫 번째 책")
    await gateways.commit()

    shelf = await autobiography_service.list_finished_autobiographies(gateways, user.id)

    assert [a.id for a in shelf] == [finished.id]
    assert unfinished.id not in [a.id for a in shelf]


@pytest.mark.asyncio
async def test_list_finished_autobiographies_orders_newest_first() -> None:
    gateways = _build_mock_gateways()
    user = await user_service.create_user(
        gateways, UserCreate(email="shelf-order@example.com", name="테스터", password="test-password-123")
    )

    older = await gateways.autobiographies.create(user.id)
    await gateways.autobiographies.update(older.id, final_content="첫 책")
    newer = await gateways.autobiographies.create(user.id)
    await gateways.autobiographies.update(newer.id, final_content="두번째 책")
    await gateways.commit()
    # 생성 시각이 같은 밀리초에 몰릴 수 있어(Windows datetime 해상도), created_at을
    # 직접 벌려 순서를 결정적으로 만든다 — list_by_user 계열 기존 테스트와 같은 이유.
    from datetime import timedelta

    older_record = default_store.autobiographies[older.id]
    older_record.created_at = older_record.created_at - timedelta(seconds=10)

    shelf = await autobiography_service.list_finished_autobiographies(gateways, user.id)

    assert [a.id for a in shelf] == [newer.id, older.id]
