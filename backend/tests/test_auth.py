"""
회원가입/로그인/토큰 인증 회귀 테스트.

Supabase Auth는 실제 네트워크 서비스이므로(app/clients/supabase_auth.py), 테스트에서는
그 함수들을 직접 패치한다 — 기존에 app.clients.solar.chat_completion을 모킹하던 것과
동일한 관례다(tests/test_autobiography_phase34_pipeline.py 참조). `_FakeSupabaseAuth`가
이메일→계정 매핑을 인메모리로 흉내 내고, 발급하는 access_token은 테스트 전용
SUPABASE_JWT_SECRET으로 실제 서명한 JWT라 app/core/security.py의 검증 로직까지
그대로 통과한다(즉 "토큰 형식만 맞는 가짜"가 아니라 실제 검증 경로를 타는 진짜 토큰).

라우터 단의 소유권 검증(다른 유저의 리소스 접근 차단)은 FastAPI TestClient + Mock DB
백엔드로 검증한다. Mock DB 백엔드는 프로세스 전역 싱글턴(app.gateways.mock.store.
default_store)을 쓰므로(app/gateways/mock/store.py 참조), 이 파일의 각 테스트는
실행 전 DB 스토어와 페이크 Supabase Auth 스토어를 모두 비워 서로 격리한다.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.clients import supabase_auth
from app.config import settings
from app.core.security import InvalidTokenError, decode_access_token
from app.gateways.mock.store import default_store
from app.main import app

_TEST_JWT_SECRET = "test-only-secret-do-not-use-elsewhere"

# 아래 _patch_supabase_auth(autouse=True)가 supabase_auth.admin_create_user 등을
# 라우터 레벨 테스트를 위해 가짜 함수로 통째로 덮어쓴다. 모듈 임포트 시점(=어떤
# monkeypatch보다도 먼저)에 진짜 함수 객체를 따로 잡아두지 않으면, "진짜 HTTP 클라이언트
# 동작(에러 매핑)"을 검증하려는 아래 client-level 테스트들도 가짜 함수를 호출하게 된다.
_real_admin_create_user = supabase_auth.admin_create_user
_real_sign_in_with_password = supabase_auth.sign_in_with_password


class _FakeSupabaseAuth:
    """auth.users를 이메일 하나당 하나씩 담는 인메모리 스토어. admin_create_user/
    sign_in_with_password/refresh_access_token과 동일한 계약(성공 시 반환값, 실패 시
    SupabaseAuthError)을 흉내 낸다."""

    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = {}  # email -> {id, password}
        self.refresh_tokens: dict[str, str] = {}  # refresh_token -> email

    def _issue_session(self, email: str) -> dict[str, Any]:
        user_id = self.accounts[email]["id"]
        now = int(time.time())
        access_token = pyjwt.encode(
            {"sub": str(user_id), "aud": "authenticated", "email": email, "iat": now, "exp": now + 3600},
            _TEST_JWT_SECRET,
            algorithm="HS256",
        )
        refresh_token = f"fake-refresh-{uuid.uuid4()}"
        self.refresh_tokens[refresh_token] = email
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 3600,
            "token_type": "bearer",
            "user": {"id": str(user_id), "email": email},
        }

    async def admin_create_user(self, *, email: str, password: str, user_metadata: dict) -> uuid.UUID:
        if email in self.accounts:
            raise supabase_auth.SupabaseAuthError(409, "이미 등록된 이메일입니다.")
        user_id = uuid.uuid4()
        self.accounts[email] = {"id": user_id, "password": password}
        return user_id

    async def sign_in_with_password(self, *, email: str, password: str) -> dict[str, Any]:
        account = self.accounts.get(email)
        if account is None or account["password"] != password:
            raise supabase_auth.SupabaseAuthError(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
        return self._issue_session(email)

    async def refresh_access_token(self, *, refresh_token: str) -> dict[str, Any]:
        email = self.refresh_tokens.get(refresh_token)
        if email is None:
            raise supabase_auth.SupabaseAuthError(401, "리프레시 토큰이 유효하지 않습니다.")
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
    # 실제 .env에 이 값들이 비어 있을 수 있는데(운영 키를 아직 안 받음), 비어 있으면
    # "Authorization: Bearer " 헤더가 공백만 남아 httpx가 LocalProtocolError를 던진다
    # (아래 supabase_auth 클라이언트 단위 테스트에서 실제로 이 헤더를 구성하므로).
    # 테스트는 .env의 실제 값과 무관하게 항상 통과해야 하므로 더미 값으로 고정한다.
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
    # SUPABASE_URL이 비어 있으면(.env 미설정) f"{settings.SUPABASE_URL}/auth/v1/..."가
    # 상대경로가 되어, httpx가 MockTransport 응답의 쿠키를 추출하려다 urllib에서
    # "unknown url type"으로 죽는다(client-level 단위 테스트에서 실제로 재현됨) —
    # 이 값도 실제 .env와 무관하게 항상 절대 URL이 되도록 고정한다.
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://test.supabase.co")
    fake = _FakeSupabaseAuth()
    monkeypatch.setattr(supabase_auth, "admin_create_user", fake.admin_create_user)
    monkeypatch.setattr(supabase_auth, "sign_in_with_password", fake.sign_in_with_password)
    monkeypatch.setattr(supabase_auth, "refresh_access_token", fake.refresh_access_token)
    yield fake


@pytest.fixture
def client():
    return TestClient(app)


# --------------------------------------------------------------------------- #
# app.core.security 단위 테스트 (실제 서명 검증 로직)                          #
# --------------------------------------------------------------------------- #


def test_decode_valid_supabase_style_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", _TEST_JWT_SECRET)
    user_id = uuid.uuid4()
    now = int(time.time())
    token = pyjwt.encode(
        {"sub": str(user_id), "aud": "authenticated", "iat": now, "exp": now + 60},
        _TEST_JWT_SECRET,
        algorithm="HS256",
    )
    assert decode_access_token(token) == user_id


def test_decode_rejects_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """aud가 'authenticated'가 아니면(예: Supabase의 anon/service_role 키 자체를
    세션 토큰으로 착각해 보낸 경우) 거부해야 한다."""
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", _TEST_JWT_SECRET)
    now = int(time.time())
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "anon", "iat": now, "exp": now + 60},
        _TEST_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_decode_rejects_garbage_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", _TEST_JWT_SECRET)
    with pytest.raises(InvalidTokenError):
        decode_access_token("not-a-real-jwt")


# --------------------------------------------------------------------------- #
# app.clients.supabase_auth 단위 테스트 (httpx 레벨, 에러 매핑 검증)           #
# --------------------------------------------------------------------------- #

# httpx는 프로세스 전역에 단 하나의 모듈 객체이므로, "app.clients.supabase_auth.httpx
# .AsyncClient"를 몽키패치하면 httpx.AsyncClient 자체가 바뀐다. 대체용 람다 안에서
# 다시 httpx.AsyncClient(...)를 부르면 그 람다 자신을 재귀 호출하게 되므로, 패치되기
# 전의 진짜 클래스를 미리 잡아둔다.
_real_async_client_cls = httpx.AsyncClient


def _mock_transport_client_factory(handler):
    return lambda **kwargs: _real_async_client_cls(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_admin_create_user_maps_duplicate_email_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"msg": "A user with this email address has already been registered"})

    monkeypatch.setattr(
        "app.clients.supabase_auth.httpx.AsyncClient", _mock_transport_client_factory(handler)
    )
    with pytest.raises(supabase_auth.SupabaseAuthError) as exc_info:
        await _real_admin_create_user(email="dupe@example.com", password="pw", user_metadata={})
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_sign_in_maps_bad_credentials_to_401(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_description": "Invalid login credentials"})

    monkeypatch.setattr(
        "app.clients.supabase_auth.httpx.AsyncClient", _mock_transport_client_factory(handler)
    )
    with pytest.raises(supabase_auth.SupabaseAuthError) as exc_info:
        await _real_sign_in_with_password(email="a@example.com", password="wrong")
    assert exc_info.value.status_code == 401


# --------------------------------------------------------------------------- #
# 회원가입 / 로그인 / 토큰 갱신 (라우터 레벨)                                  #
# --------------------------------------------------------------------------- #


def test_signup_then_login_returns_token(client: TestClient) -> None:
    signup = client.post(
        "/api/v1/users",
        json={"email": "auth-test@example.com", "name": "인증테스트", "password": "password123"},
    )
    assert signup.status_code == 201, signup.text
    body = signup.json()
    assert "password" not in body and "hashed_password" not in body  # 응답에 절대 노출 금지

    login = client.post(
        "/api/v1/auth/login", json={"email": "auth-test@example.com", "password": "password123"}
    )
    assert login.status_code == 200, login.text
    token_body = login.json()
    assert token_body["token_type"] == "bearer"
    assert token_body["expires_in"] > 0
    assert token_body["access_token"] and token_body["refresh_token"]


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    client.post(
        "/api/v1/users",
        json={"email": "auth-test2@example.com", "name": "인증테스트2", "password": "password123"},
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "auth-test2@example.com", "password": "wrong-password"}
    )
    assert resp.status_code == 401


def test_login_unknown_email_returns_401_not_404(client: TestClient) -> None:
    """계정 존재 여부를 노출하지 않기 위해 이메일 미존재도 401로 응답해야 한다
    (auth_service.InvalidCredentialsError 참조 — 사용자 열거 공격 방지)."""
    resp = client.post(
        "/api/v1/auth/login", json={"email": "no-such-user@example.com", "password": "whatever123"}
    )
    assert resp.status_code == 401


def test_signup_duplicate_email_returns_409(client: TestClient) -> None:
    payload = {"email": "dupe@example.com", "name": "중복", "password": "password123"}
    first = client.post("/api/v1/users", json=payload)
    assert first.status_code == 201
    second = client.post("/api/v1/users", json=payload)
    assert second.status_code == 409


def test_refresh_token_issues_new_access_token(client: TestClient) -> None:
    client.post(
        "/api/v1/users",
        json={"email": "refresh-test@example.com", "name": "리프레시", "password": "password123"},
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": "refresh-test@example.com", "password": "password123"}
    ).json()

    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]


def test_refresh_with_invalid_token_returns_401(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-real-refresh-token"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 인증 토큰 요구 / 소유권 검증                                                 #
# --------------------------------------------------------------------------- #


def test_protected_endpoint_without_token_is_rejected(client: TestClient) -> None:
    resp = client.get(f"/api/v1/users/{uuid.uuid4()}")
    assert resp.status_code in (401, 403)  # HTTPBearer 기본 동작은 403


def test_get_me_returns_current_user(client: TestClient) -> None:
    client.post(
        "/api/v1/users",
        json={"email": "me-test@example.com", "name": "미투데스트", "password": "password123"},
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": "me-test@example.com", "password": "password123"}
    )
    token = login.json()["access_token"]

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me-test@example.com"


def test_cannot_read_another_users_profile(client: TestClient) -> None:
    """A로 로그인해서 B의 user_id로 GET /users/{id}를 호출하면 403이어야 한다 —
    이게 이번 인증 작업의 핵심 목적(리소스 소유권 강제)이다."""
    a = client.post(
        "/api/v1/users", json={"email": "user-a@example.com", "name": "A", "password": "password123"}
    ).json()
    b = client.post(
        "/api/v1/users", json={"email": "user-b@example.com", "name": "B", "password": "password123"}
    ).json()
    token_a = client.post(
        "/api/v1/auth/login", json={"email": "user-a@example.com", "password": "password123"}
    ).json()["access_token"]

    own_profile = client.get(f"/api/v1/users/{a['id']}", headers={"Authorization": f"Bearer {token_a}"})
    assert own_profile.status_code == 200

    other_profile = client.get(f"/api/v1/users/{b['id']}", headers={"Authorization": f"Bearer {token_a}"})
    assert other_profile.status_code == 403


def test_interview_session_created_for_current_user_not_spoofable(client: TestClient) -> None:
    """세션 생성 요청 바디에는 user_id가 없다(app/schemas/interview.py) — 토큰의
    소유자로만 생성되는지 확인."""
    signup = client.post(
        "/api/v1/users",
        json={"email": "session-owner@example.com", "name": "세션주인", "password": "password123"},
    ).json()
    token = client.post(
        "/api/v1/auth/login", json={"email": "session-owner@example.com", "password": "password123"}
    ).json()["access_token"]

    created = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["user_id"] == signup["id"]


def test_signup_with_non_duplicate_supabase_error_returns_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이메일 중복(409)이 아닌 다른 Supabase Auth 거부 사유(예: 비밀번호 정책 위반)는
    처리되지 않은 예외로 새어나가 500이 되는 대신 400으로 응답해야 한다
    (app/services/user_service.py InvalidSignupError, app/clients/supabase_auth.py 참조)."""

    async def _reject(*, email: str, password: str, user_metadata: dict) -> None:
        raise supabase_auth.SupabaseAuthError(422, "Password should be at least 6 characters")

    monkeypatch.setattr(supabase_auth, "admin_create_user", _reject)

    resp = client.post(
        "/api/v1/users",
        json={"email": "weak-pw@example.com", "name": "약한비번", "password": "password1"},
    )
    assert resp.status_code == 400


def test_cannot_attach_media_to_another_users_session(client: TestClient) -> None:
    """세션 소유자가 아닌 사용자가 그 session_id로 사진을 업로드하려 하면 404여야 한다
    (app/api/v1/media.py 소유권 검증 — 없으면 타인의 인터뷰 세션에 미디어를 연결시킬
    수 있었다)."""
    client.post(
        "/api/v1/users",
        json={"email": "media-owner@example.com", "name": "미디어주인", "password": "password123"},
    )
    owner_token = client.post(
        "/api/v1/auth/login", json={"email": "media-owner@example.com", "password": "password123"}
    ).json()["access_token"]
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()

    client.post(
        "/api/v1/users",
        json={"email": "media-intruder@example.com", "name": "미디어침입자", "password": "password123"},
    )
    intruder_token = client.post(
        "/api/v1/auth/login", json={"email": "media-intruder@example.com", "password": "password123"}
    ).json()["access_token"]

    # asset_type=document로 업로드해 듀얼 트랙 분석(Azure Vision/Solar 실호출)을
    # 건드리지 않는다 — 이 테스트의 관심사는 오직 session_id 소유권 검증이다.
    resp = client.post(
        "/api/v1/media-assets",
        data={"session_id": session["id"], "asset_type": "document"},
        files={"file": ("note.txt", b"hello", "text/plain")},
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert resp.status_code == 404


def test_cannot_read_another_users_interview_session(client: TestClient) -> None:
    client.post(
        "/api/v1/users",
        json={"email": "owner2@example.com", "name": "오너", "password": "password123"},
    )
    owner_login = client.post(
        "/api/v1/auth/login", json={"email": "owner2@example.com", "password": "password123"}
    ).json()["access_token"]
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers={"Authorization": f"Bearer {owner_login}"},
    ).json()

    client.post(
        "/api/v1/users",
        json={"email": "intruder@example.com", "name": "침입자", "password": "password123"},
    )
    intruder_token = client.post(
        "/api/v1/auth/login", json={"email": "intruder@example.com", "password": "password123"}
    ).json()["access_token"]

    resp = client.get(
        f"/api/v1/interview-sessions/{session['id']}",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert resp.status_code == 404  # 존재를 숨기기 위해 403이 아닌 404 (app/api/v1/interviews.py 참조)


# --------------------------------------------------------------------------- #
# 프로필 부분 수정 (PATCH /users/{user_id})                                    #
# --------------------------------------------------------------------------- #


def _signup_and_login(client: TestClient, *, email: str, name: str) -> tuple[str, str]:
    """POST /users로 가입한 뒤 로그인해 (user_id, access_token)을 돌려준다."""
    signup = client.post(
        "/api/v1/users", json={"email": email, "name": name, "password": "password123"}
    )
    assert signup.status_code == 201, signup.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    return signup.json()["id"], login.json()["access_token"]


def test_update_profile_fills_birth_year_and_hometown(client: TestClient) -> None:
    """가입 시점에 안 받은 생년/고향을 로그인 이후 PATCH로 채우는 경로."""
    user_id, token = _signup_and_login(client, email="patch-me@example.com", name="패치유저")

    patched = client.patch(
        f"/api/v1/users/{user_id}",
        json={"birth_year": 1958, "hometown": "여수"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["birth_year"] == 1958
    assert patched.json()["hometown"] == "여수"
    assert patched.json()["name"] == "패치유저"  # 안 보낸 필드는 그대로 유지


def test_cannot_patch_another_users_profile(client: TestClient) -> None:
    _, token_a = _signup_and_login(client, email="patch-a@example.com", name="A")
    user_b_id, _ = _signup_and_login(client, email="patch-b@example.com", name="B")

    resp = client.patch(
        f"/api/v1/users/{user_b_id}",
        json={"hometown": "몰래"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
