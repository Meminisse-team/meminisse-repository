"""고정 인터뷰 질문 큐(Question 테이블, docs/QUESTION_BANK_GUIDE.md) 배선 테스트.

핵심 계약: FIXED_QUESTION 세션을 question_id 없이 만들면 sequence_order가 가장
빠른 미배정 활성 질문이 자동으로 붙고, 그 세션의 슬롯이 다 채워지면 세션이 자동
완료되며 다음 질문이 미리보기로 보이고, 큐를 전부 마치면 명확한 신호(409)를 준다.

_patch_supabase_auth/_reset_mock_store/client 픽스처는 tests/test_auth.py,
tests/test_list_endpoints.py와 동일한 패턴을 그대로 복제한다 — 이 프로젝트는
conftest.py에 공용 픽스처를 두지 않고 테스트 모듈마다 자체적으로 둔다.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import patch

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.agents import prompts
from app.clients import supabase_auth
from app.config import settings
from app.gateways.mock.store import default_store
from app.main import app

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


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    """슬롯 게이팅 응답을 가짜로 흉내낸다 — 필수 슬롯을 전부 채웠다고 응답해
    add_user_turn의 "슬롯 충족" 분기(질문 큐 전진)를 즉시 타게 한다."""
    return {"newly_filled_slots": list(prompts.REQUIRED_SLOTS.keys())}


@pytest.fixture(autouse=True)
def _reset_mock_store():
    default_store.users.clear()
    default_store.sessions.clear()
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


@pytest.fixture(autouse=True)
def _skip_celery_queueing():
    """세션 완료(complete_session)는 실제 Celery 브로커(Redis)에 `.delay()`로
    큐잉을 시도한다 — 로컬에 Redis가 안 떠 있으면(이 테스트 환경 포함) 매 호출마다
    연결 타임아웃만큼 멎는다(app/services/interview_service.py:complete_session
    참조, 실패는 잡아서 경고만 남기므로 테스트 자체는 통과하지만 몇 분씩 걸린다).
    질문 큐 배선 자체와는 무관한 인프라 의존성이라 여기서는 잘라낸다."""
    with patch("app.workers.tasks.process_session_completion.delay"):
        yield


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


def test_create_fixed_question_session_auto_assigns_first_question(client: TestClient) -> None:
    _, token = _signup_and_login(client, "queue-a@example.com")

    resp = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["question_id"] is not None

    assigned = default_store.questions[uuid.UUID(body["question_id"])]
    assert assigned.sequence_order == 1


def test_completing_a_question_advances_the_queue(client: TestClient) -> None:
    _, token = _signup_and_login(client, "queue-b@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()
    first_question = default_store.questions[uuid.UUID(session["question_id"])]
    assert first_question.sequence_order == 1

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        turn = client.post(
            f"/api/v1/interview-sessions/{session['id']}/messages",
            json={"content": "부산에서 살던 시절 혼자 겪었던 일이에요."},
            headers=_auth_headers(token),
        )
    assert turn.status_code == 200
    turn_body = turn.json()
    # 슬롯이 다 채워졌으므로 세션이 자동 완료되고, 다음 질문(sequence_order=2)이
    # 응답 메시지에 미리보기로 담겨야 한다.
    assert turn_body["session"]["status"] == "completed"
    second_question = next(
        q for q in default_store.questions.values() if q.sequence_order == 2
    )
    assert second_question.content in turn_body["assistant_message"]["content"]

    # 다음 세션 생성 시 question_id를 안 넘기면 방금 완료한 질문이 아니라
    # 그 다음(sequence_order=2) 질문이 배정되어야 한다.
    next_session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()
    assert next_session["question_id"] == str(second_question.id)


def test_exhausting_the_queue_returns_409(client: TestClient) -> None:
    _, token = _signup_and_login(client, "queue-c@example.com")

    # 큐를 끝까지 소진: 매번 새 세션을 만들고 곧바로 슬롯을 채워 자동 완료시킨다.
    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        for _ in range(len(default_store.questions)):
            session = client.post(
                "/api/v1/interview-sessions",
                json={"session_type": "fixed_question"},
                headers=_auth_headers(token),
            ).json()
            client.post(
                f"/api/v1/interview-sessions/{session['id']}/messages",
                json={"content": "혼자 겪었던 일이에요."},
                headers=_auth_headers(token),
            )

    resp = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 409


def test_explicit_question_id_bypasses_auto_assignment(client: TestClient) -> None:
    _, token = _signup_and_login(client, "queue-d@example.com")
    chosen = next(q for q in default_store.questions.values() if q.sequence_order == 5)

    resp = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question", "question_id": str(chosen.id)},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 201
    assert resp.json()["question_id"] == str(chosen.id)
