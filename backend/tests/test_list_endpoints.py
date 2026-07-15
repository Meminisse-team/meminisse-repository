"""
GET /interview-sessions, GET /media-assets, GET /events(목록 조회 3종) 회귀 테스트.

핵심 검증 대상은 두 가지: (1) 정렬 순서가 계약대로인지, (2) 무엇보다 다른 사용자의
데이터가 절대 섞여 나오지 않는지(테넌트 격리) — 목록 엔드포인트는 소유권 검증이
"내 것이 아니면 404" 한 건이 아니라 "전체 목록에서 남의 것을 걸러내는" 형태라
개별 리소스 라우터보다 데이터 누출 위험이 실질적으로 더 크다.

Mock DB 백엔드(app.gateways.mock.store.default_store)에 직접 시드하고 TestClient로
조회하는 방식을 쓴다 — Event/MediaAsset 생성 파이프라인 전체(Celery, Upstage 호출)를
HTTP로 다시 밟을 필요 없이 목록 엔드포인트 자체의 계약만 검증하기 위함이다.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.clients import supabase_auth
from app.config import settings
from app.gateways.dto import EventCreateData, MediaAssetCreateData
from app.gateways.mock.gateways import MockEventGateway, MockMediaAssetGateway
from app.gateways.mock.store import default_store
from app.main import app
from app.models.enums import AssetType, EventSourceType

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
    """(user_id, access_token) 반환."""
    user = client.post(
        "/api/v1/users", json={"email": email, "name": email.split("@")[0], "password": "password123"}
    ).json()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"}).json()[
        "access_token"
    ]
    return user["id"], token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# GET /interview-sessions                                                     #
# --------------------------------------------------------------------------- #


def test_list_sessions_only_returns_own_sessions_newest_first(client: TestClient) -> None:
    a_id, a_token = _signup_and_login(client, "a@example.com")
    _, b_token = _signup_and_login(client, "b@example.com")

    client.post(
        "/api/v1/interview-sessions", json={"session_type": "fixed_question"}, headers=_auth_headers(a_token)
    )
    second = client.post(
        "/api/v1/interview-sessions", json={"session_type": "fixed_question"}, headers=_auth_headers(a_token)
    ).json()
    client.post(
        "/api/v1/interview-sessions", json={"session_type": "fixed_question"}, headers=_auth_headers(b_token)
    )

    resp = client.get("/api/v1/interview-sessions", headers=_auth_headers(a_token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(s["user_id"] == a_id for s in body)
    assert body[0]["id"] == second["id"]  # 최신순(started_at desc) — 두 번째로 만든 게 먼저


def test_session_detail_includes_chat_logs_list_does_not(client: TestClient) -> None:
    _, token = _signup_and_login(client, "detail@example.com")
    session = client.post(
        "/api/v1/interview-sessions", json={"session_type": "fixed_question"}, headers=_auth_headers(token)
    ).json()

    list_body = client.get("/api/v1/interview-sessions", headers=_auth_headers(token)).json()
    assert "chat_logs" not in list_body[0]

    detail = client.get(f"/api/v1/interview-sessions/{session['id']}", headers=_auth_headers(token))
    assert detail.status_code == 200
    # 세션 생성 시 질문 문구가 chat_log(role=assistant)로 자동 저장된다(2026-07-15
    # — 세션 종료 후 산문 재조립 시 "무엇에 대한 답인지" 맥락을 보존하기 위함,
    # interview_service.py:_resolve_opening_content). 아직 사용자 발화는 없으므로
    # 이 한 건뿐이어야 한다.
    chat_logs = detail.json()["chat_logs"]
    assert len(chat_logs) == 1
    assert chat_logs[0]["role"] == "assistant"


# --------------------------------------------------------------------------- #
# GET /media-assets                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_media_asset_gateway_list_by_user_scopes_correctly() -> None:
    gw = MockMediaAssetGateway(default_store)
    a_id, b_id = uuid.uuid4(), uuid.uuid4()
    await gw.create(
        MediaAssetCreateData(user_id=a_id, s3_key="a/1.jpg", s3_url="https://x/a1", asset_type=AssetType.IMAGE)
    )
    await gw.create(
        MediaAssetCreateData(user_id=a_id, s3_key="a/2.jpg", s3_url="https://x/a2", asset_type=AssetType.IMAGE)
    )
    await gw.create(
        MediaAssetCreateData(user_id=b_id, s3_key="b/1.jpg", s3_url="https://x/b1", asset_type=AssetType.IMAGE)
    )

    a_assets = await gw.list_by_user(a_id)
    assert len(a_assets) == 2
    assert all(asset.user_id == a_id for asset in a_assets)
    assert len(await gw.list_by_user(b_id)) == 1


def test_media_assets_router_scopes_by_current_user(client: TestClient) -> None:
    a_id, a_token = _signup_and_login(client, "photos-a@example.com")
    b_id, _ = _signup_and_login(client, "photos-b@example.com")

    # 업로드 라우터는 Azure Vision/Solar를 호출하므로(파일 업로드 파이프라인), 여기서는
    # 목록 엔드포인트의 소유권 스코프만 검증하기 위해 스토어에 직접 시드한다 — user_id는
    # 실제 로그인 사용자 id를 그대로 써서 라우터가 current_user.id로 필터링하는지 확인한다.
    gw = MockMediaAssetGateway(default_store)
    import asyncio

    asyncio.run(
        gw.create(
            MediaAssetCreateData(
                user_id=uuid.UUID(a_id), s3_key="k1", s3_url="https://x/1", asset_type=AssetType.IMAGE
            )
        )
    )
    asyncio.run(
        gw.create(
            MediaAssetCreateData(
                user_id=uuid.UUID(b_id), s3_key="k2", s3_url="https://x/2", asset_type=AssetType.IMAGE
            )
        )
    )

    resp = client.get("/api/v1/media-assets", headers=_auth_headers(a_token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["user_id"] == a_id


def test_get_media_asset_by_id_scopes_by_current_user(client: TestClient) -> None:
    """PHOTO 세션 채팅 화면이 linked_media_asset_id로 사진 원본을 조회하는 GET
    /media-assets/{id}도 목록 엔드포인트와 동일하게 소유권 밖이면 404여야 한다."""
    a_id, a_token = _signup_and_login(client, "photo-detail-a@example.com")
    b_id, b_token = _signup_and_login(client, "photo-detail-b@example.com")

    gw = MockMediaAssetGateway(default_store)
    import asyncio

    asset = asyncio.run(
        gw.create(
            MediaAssetCreateData(
                user_id=uuid.UUID(a_id), s3_key="k1", s3_url="https://x/1", asset_type=AssetType.IMAGE
            )
        )
    )

    own_resp = client.get(f"/api/v1/media-assets/{asset.id}", headers=_auth_headers(a_token))
    assert own_resp.status_code == 200
    assert own_resp.json()["id"] == str(asset.id)

    other_resp = client.get(f"/api/v1/media-assets/{asset.id}", headers=_auth_headers(b_token))
    assert other_resp.status_code == 404

    missing_resp = client.get(f"/api/v1/media-assets/{uuid.uuid4()}", headers=_auth_headers(a_token))
    assert missing_resp.status_code == 404


# --------------------------------------------------------------------------- #
# GET /events                                                                 #
# --------------------------------------------------------------------------- #


def test_events_router_excludes_unverified_and_other_users(client: TestClient) -> None:
    a_id, a_token = _signup_and_login(client, "events-a@example.com")
    b_id, _ = _signup_and_login(client, "events-b@example.com")

    gw = MockEventGateway(default_store)
    import asyncio

    async def seed():
        # A의 검증된 사건 (목록에 나와야 함)
        await gw.bulk_create(
            [
                EventCreateData(
                    user_id=uuid.UUID(a_id),
                    source_type=EventSourceType.SESSION_CHAT,
                    one_line_summary="부산 출생",
                    prose_paragraph="나는 부산에서 태어났다.",
                    verified=True,
                )
            ]
        )
        # A의 미검증 사건 (OCR 의심 격리 — 목록에서 빠져야 함)
        await gw.create(
            EventCreateData(
                user_id=uuid.UUID(a_id),
                source_type=EventSourceType.DOCUMENT,
                one_line_summary="의심 구간",
                prose_paragraph="OCR 의심 원문",
                verified=False,
            )
        )
        # B의 검증된 사건 (A 목록에 나오면 안 됨)
        await gw.bulk_create(
            [
                EventCreateData(
                    user_id=uuid.UUID(b_id),
                    source_type=EventSourceType.SESSION_CHAT,
                    one_line_summary="B의 사건",
                    prose_paragraph="B의 산문",
                    verified=True,
                )
            ]
        )

    asyncio.run(seed())

    resp = client.get("/api/v1/events", headers=_auth_headers(a_token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["one_line_summary"] == "부산 출생"
    assert "verified" not in body[0]  # 내부 필드는 응답 스키마에서 제외됨
