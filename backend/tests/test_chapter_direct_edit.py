"""'나의 자서전' 직접 수정 기능(2026-07-18) 회귀 테스트.

완성된 자서전(final_content 존재)의 챕터 본문을 사용자가 직접 고쳐 저장하는
PATCH /{autobiography_id}/chapters/{chapter_draft_id}/content 엔드포인트와
서비스 함수 autobiography_service.edit_chapter_content를 검증한다.

핵심 요구사항: 이 경로는 LLM/외부 API 호출이 전혀 없어야 한다 — 세션 대화 저장
경로(interview_service.add_user_turn)에서 예전에 실제로 겪었던 "느린 외부 호출을
기다리며 DB 트랜잭션을 오래 열어둬 Supabase가 idle 커넥션을 끊어버리는" 문제를
재발시키지 않기 위함이다. solar.chat_completion/structured_completion을 호출하면
바로 실패하는 가짜로 패치해 이 계약을 직접 증명한다.
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
from app.gateways.dto import ChapterDraftCreateData
from app.gateways.factory import Gateways, _build_mock_gateways
from app.gateways.mock.store import default_store
from app.main import app
from app.services import autobiography_service

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


async def _seed_finalized_autobiography(user_id: uuid.UUID) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """final_content가 있는(=완성된) 자서전 하나와 챕터 2개를 직접 심는다 —
    전체 Phase 3/4 파이프라인을 돌리지 않고 이 기능만 겨냥한 최소 상태."""
    gateways: Gateways = _build_mock_gateways()
    autobiography = await gateways.autobiographies.create(user_id)
    chapters = await gateways.chapters.replace_all(
        autobiography.id,
        [
            ChapterDraftCreateData(chapter_index=1, title="첫 만남", synopsis="시놉시스1"),
            ChapterDraftCreateData(chapter_index=2, title="새로운 시작", synopsis="시놉시스2"),
        ],
    )
    for chapter, content in zip(chapters, ["1장 원본 본문.", "2장 원본 본문."]):
        await gateways.chapters.update_content(chapter.id, content)
    final_content = "[1장. 첫 만남]\n1장 원본 본문.\n\n[2장. 새로운 시작]\n2장 원본 본문."
    await gateways.autobiographies.update(autobiography.id, final_content=final_content)
    await gateways.commit()
    return autobiography.id, [c.id for c in chapters]


def _fail_if_called(*args, **kwargs):
    raise AssertionError("직접 수정 경로에서 LLM 호출이 발생했다 — 이 기능은 순수 텍스트 저장이어야 한다.")


@pytest.mark.asyncio
async def test_edit_chapter_content_updates_chapter_and_rejoins_final_content() -> None:
    """서비스 레이어: 챕터 본문을 바꾸면 final_content도 그 챕터만 새 내용으로
    교체된 형태로 재조립돼야 한다(다른 챕터는 그대로)."""
    gateways: Gateways = _build_mock_gateways()
    user = await gateways.users.create.__self__.create(  # type: ignore[attr-defined]
        __import__("app.gateways.dto", fromlist=["UserCreateData"]).UserCreateData(
            id=uuid.uuid4(), email="edit-svc@example.com", name="테스터"
        )
    )
    await gateways.commit()

    autobiography = await gateways.autobiographies.create(user.id)
    chapters = await gateways.chapters.replace_all(
        autobiography.id,
        [
            ChapterDraftCreateData(chapter_index=1, title="첫 만남"),
            ChapterDraftCreateData(chapter_index=2, title="새로운 시작"),
        ],
    )
    for chapter, content in zip(chapters, ["1장 원본.", "2장 원본."]):
        await gateways.chapters.update_content(chapter.id, content)
    await gateways.autobiographies.update(
        autobiography.id,
        final_content="[1장. 첫 만남]\n1장 원본.\n\n[2장. 새로운 시작]\n2장 원본.",
    )
    await gateways.commit()

    with (
        patch("app.clients.solar.chat_completion", side_effect=_fail_if_called),
        patch("app.clients.solar.structured_completion", side_effect=_fail_if_called),
    ):
        updated = await autobiography_service.edit_chapter_content(
            gateways, autobiography.id, chapters[0].id, "1장을 사용자가 직접 고친 본문."
        )

    assert "1장을 사용자가 직접 고친 본문." in updated.final_content
    assert "2장 원본." in updated.final_content  # 다른 챕터는 그대로

    refreshed_chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    edited = next(c for c in refreshed_chapters if c.id == chapters[0].id)
    assert edited.content == "1장을 사용자가 직접 고친 본문."


@pytest.mark.asyncio
async def test_edit_chapter_content_rejects_chapter_from_other_autobiography() -> None:
    gateways: Gateways = _build_mock_gateways()
    user = await gateways.users.create(
        __import__("app.gateways.dto", fromlist=["UserCreateData"]).UserCreateData(
            id=uuid.uuid4(), email="edit-mismatch@example.com", name="테스터"
        )
    )
    await gateways.commit()

    auto_a = await gateways.autobiographies.create(user.id)
    auto_b_user = await gateways.users.create(
        __import__("app.gateways.dto", fromlist=["UserCreateData"]).UserCreateData(
            id=uuid.uuid4(), email="edit-mismatch-b@example.com", name="테스터B"
        )
    )
    await gateways.commit()
    auto_b = await gateways.autobiographies.create(auto_b_user.id)
    chapters_b = await gateways.chapters.replace_all(
        auto_b.id, [ChapterDraftCreateData(chapter_index=1, title="B의 챕터")]
    )
    await gateways.commit()

    with pytest.raises(ValueError):
        await autobiography_service.edit_chapter_content(
            gateways, auto_a.id, chapters_b[0].id, "다른 자서전 챕터를 고치려는 시도."
        )


def test_patch_endpoint_requires_finalized_autobiography(client: TestClient) -> None:
    """최종 윤문(final_content)이 아직 없으면 409 — 라우터 레벨 선행 조건."""
    import asyncio

    user_id, token = _signup_and_login(client, "notfinal@example.com")

    async def _seed_unfinalized():
        gateways: Gateways = _build_mock_gateways()
        autobiography = await gateways.autobiographies.create(uuid.UUID(user_id))
        chapters = await gateways.chapters.replace_all(
            autobiography.id, [ChapterDraftCreateData(chapter_index=1, title="1장")]
        )
        await gateways.chapters.update_content(chapters[0].id, "아직 윤문 전 본문.")
        await gateways.commit()
        return autobiography.id, chapters[0].id

    autobiography_id, chapter_id = asyncio.run(_seed_unfinalized())

    resp = client.patch(
        f"/api/v1/autobiographies/{autobiography_id}/chapters/{chapter_id}/content",
        json={"content": "직접 고친 내용"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 409


def test_patch_endpoint_saves_edit_without_llm_calls_and_rejoins_final_content(
    client: TestClient,
) -> None:
    import asyncio

    user_id, token = _signup_and_login(client, "editor@example.com")
    autobiography_id, chapter_ids = asyncio.run(_seed_finalized_autobiography(uuid.UUID(user_id)))

    with (
        patch("app.clients.solar.chat_completion", side_effect=_fail_if_called),
        patch("app.clients.solar.structured_completion", side_effect=_fail_if_called),
    ):
        resp = client.patch(
            f"/api/v1/autobiographies/{autobiography_id}/chapters/{chapter_ids[0]}/content",
            json={"content": "사용자가 직접 고친 1장 본문입니다."},
            headers=_auth_headers(token),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "사용자가 직접 고친 1장 본문입니다." in body["final_content"]
    assert "2장 원본 본문." in body["final_content"]


def test_patch_endpoint_rejects_other_users_autobiography(client: TestClient) -> None:
    import asyncio

    owner_id, _ = _signup_and_login(client, "owner@example.com")
    _, intruder_token = _signup_and_login(client, "intruder@example.com")
    autobiography_id, chapter_ids = asyncio.run(_seed_finalized_autobiography(uuid.UUID(owner_id)))

    resp = client.patch(
        f"/api/v1/autobiographies/{autobiography_id}/chapters/{chapter_ids[0]}/content",
        json={"content": "침입자가 고치려는 내용"},
        headers=_auth_headers(intruder_token),
    )
    assert resp.status_code == 404


def test_patch_endpoint_rejects_empty_content(client: TestClient) -> None:
    import asyncio

    user_id, token = _signup_and_login(client, "empty@example.com")
    autobiography_id, chapter_ids = asyncio.run(_seed_finalized_autobiography(uuid.UUID(user_id)))

    resp = client.patch(
        f"/api/v1/autobiographies/{autobiography_id}/chapters/{chapter_ids[0]}/content",
        json={"content": ""},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 422
