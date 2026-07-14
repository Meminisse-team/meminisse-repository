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


# interview_service.MIN_RICH_ANSWER_LENGTH(구체화 재질문 임계값)보다 길어야 한다 —
# 짧으면 슬롯이 다 차 있어도 세션이 바로 완료되지 않고 "구체화 요청" 분기를 타
# solar.chat_completion(모킹 안 됨)을 실제로 호출하게 된다.
_RICH_ANSWER = (
    "부산에서 살던 시절 혼자 겪었던 일이에요. 그때는 부모님이 장사하시느라 바쁘셔서 "
    "동생이랑 둘이 자주 놀았는데, 그날따라 유난히 하늘이 맑았던 게 아직도 기억나요."
)


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


def _send_message(client: TestClient, token: str, session_id: str, content: str):
    return client.post(
        f"/api/v1/interview-sessions/{session_id}/messages",
        json={"content": content},
        headers=_auth_headers(token),
    )


def _complete_session_via_chat(client: TestClient, token: str, session_id: str):
    """풍부한 답변 한 번 + 마무리 확인("더 하실 말씀 있으세요?")에 짧게 답하는 턴
    하나, 총 두 턴을 보내야 실제로 세션이 완료된다(interview_service.py:
    _WRAP_UP_OFFERED_KEY, 2026-07-15 — 슬롯이 다 차고 답변도 충분히 풍부해도
    곧바로 완료되지 않고 한 번 더 확인한다). 마지막(완료) 턴의 응답을 반환한다."""
    _send_message(client, token, session_id, _RICH_ANSWER)
    return _send_message(client, token, session_id, "아니요, 없어요")


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
        turn = _complete_session_via_chat(client, token, session["id"])
    assert turn.status_code == 200
    turn_body = turn.json()
    # 슬롯이 다 채워지고(+마무리 확인까지 거쳐) 세션이 자동 완료돼야 한다.
    assert turn_body["session"]["status"] == "completed"
    second_question = next(
        q for q in default_store.questions.values() if q.sequence_order == 2
    )

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
            _complete_session_via_chat(client, token, session["id"])

    resp = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 409


def test_short_answer_triggers_elaboration_instead_of_completing(client: TestClient) -> None:
    """슬롯이 다 찼어도 이 사건에 대해 쓴 글자 수 총합이 너무 적으면(카카오톡처럼
    짧게 대답하게 되는 채팅 UI 문제 대응, 2026-07-14) 세션을 바로 완료 처리하지
    않고 한 번 더 구체화를 요청해야 한다."""
    _, token = _signup_and_login(client, "elaborate-a@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()

    async def _fake_chat_completion(messages, **kwargs):
        class _Msg:
            content = "그때 표정은 어땠어요? 조금 더 자세히 들려주세요."

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
    ):
        short_turn = client.post(
            f"/api/v1/interview-sessions/{session['id']}/messages",
            json={"content": "혼자 겪었던 일이에요."},  # MIN_RICH_ANSWER_LENGTH(80자)보다 훨씬 짧음
            headers=_auth_headers(token),
        )
        assert short_turn.status_code == 200
        short_body = short_turn.json()
        # 아직 완료되지 않았어야 하고, 다음 질문이 아니라 구체화 요청 문구가 와야 한다.
        assert short_body["session"]["status"] == "open"
        assert "표정" in short_body["assistant_message"]["content"]

        rich_turn = client.post(
            f"/api/v1/interview-sessions/{session['id']}/messages",
            json={"content": _RICH_ANSWER},
            headers=_auth_headers(token),
        )
        assert rich_turn.status_code == 200
        # 누적 글자 수(짧은 답변 + 이번 답변)가 임계값을 넘겼으므로 이제 슬롯도 다
        # 찼고 충분히 풍부하다 — 곧장 완료되지 않고 마무리 확인이 한 번 더 온다.
        rich_body = rich_turn.json()
        assert rich_body["session"]["status"] == "open"
        assert rich_body["assistant_message"]["content"] == prompts.WRAP_UP_CHECK_IN_MESSAGE

        final_turn = _send_message(client, token, session["id"], "아니요, 없어요")
    assert final_turn.json()["session"]["status"] == "completed"


def test_llm_contextual_followup_is_asked_before_wrap_up(client: TestClient) -> None:
    """슬롯도 다 차고 답변도 충분히 풍부해지면, 고정 문구 마무리 확인 전에 LLM이
    맥락을 보고 자율적으로 캐물을 지점이 있는지 먼저 판단해야 한다(2026-07-15
    피드백 — INTERVIEW_PERSONA_SYSTEM_PROMPT가 원래 표방했던 "빈틈을 알아채는"
    역할을 실제로 연결). 있다고 판단되면 그 질문을 먼저 보여주고, 세션은 아직
    끝나지 않아야 한다."""
    _, token = _signup_and_login(client, "contextual-a@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()

    async def _fake_structured_with_contextual(messages, *, schema_name, json_schema, **kwargs):
        if schema_name == "tier1_detection":
            return {"strong_negative_emotion": False}
        if schema_name == "slot_gating":
            return {"newly_filled_slots": list(prompts.REQUIRED_SLOTS.keys())}
        if schema_name == "contextual_followup":
            return {"has_followup": True, "question": "그때 아버지 표정은 어떠셨어요?"}
        raise AssertionError(f"unexpected schema_name: {schema_name}")

    with patch("app.clients.solar.structured_completion", new=_fake_structured_with_contextual):
        turn = _send_message(client, token, session["id"], _RICH_ANSWER)

    assert turn.status_code == 200
    body = turn.json()
    assert body["session"]["status"] == "open"
    assert body["assistant_message"]["content"] == "그때 아버지 표정은 어떠셨어요?"
    # 마무리 확인 문구가 아니라 맥락 꼬리질문이 나왔어야 한다.
    assert body["assistant_message"]["content"] != prompts.WRAP_UP_CHECK_IN_MESSAGE


def test_llm_contextual_followup_falls_through_to_wrap_up_in_same_turn_when_nothing_found(
    client: TestClient,
) -> None:
    """LLM이 "캐물을 것 없음"으로 판단하면, 빈 라운드트립 없이 같은 턴 안에서
    바로 마무리 확인으로 넘어가야 한다."""
    _, token = _signup_and_login(client, "contextual-b@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()

    async def _fake_structured_no_contextual(messages, *, schema_name, json_schema, **kwargs):
        if schema_name == "tier1_detection":
            return {"strong_negative_emotion": False}
        if schema_name == "slot_gating":
            return {"newly_filled_slots": list(prompts.REQUIRED_SLOTS.keys())}
        if schema_name == "contextual_followup":
            return {"has_followup": False, "question": None}
        raise AssertionError(f"unexpected schema_name: {schema_name}")

    with patch("app.clients.solar.structured_completion", new=_fake_structured_no_contextual):
        turn = _send_message(client, token, session["id"], _RICH_ANSWER)

    assert turn.status_code == 200
    body = turn.json()
    assert body["session"]["status"] == "open"
    assert body["assistant_message"]["content"] == prompts.WRAP_UP_CHECK_IN_MESSAGE


def test_session_creation_persists_opening_question_as_chat_log(client: TestClient) -> None:
    """세션 생성 시 질문 문구가 실제 chat_log(role=assistant)로 저장돼야 한다 —
    이전엔 프론트 로컬 상태로만 보여지고 DB에는 저장되지 않아, 세션 종료 후 산문
    재조립 시 "무엇에 대한 답인지" 맥락이 사라지는 문제가 있었다(2026-07-15,
    예: "대학을 어디 다녔나요?"에 "서울대"라고만 답해도 DB엔 "서울대"만 남음)."""
    _, token = _signup_and_login(client, "opening-a@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()
    first_question = default_store.questions[uuid.UUID(session["question_id"])]

    detail = client.get(
        f"/api/v1/interview-sessions/{session['id']}", headers=_auth_headers(token)
    ).json()
    assert len(detail["chat_logs"]) == 1
    assert detail["chat_logs"][0]["role"] == "assistant"
    assert detail["chat_logs"][0]["content"] == first_question.content


def test_rich_answer_prompts_wrap_up_check_before_completing(client: TestClient) -> None:
    """슬롯도 다 차고 답변도 충분히 풍부해도(구체화 요청 분기를 거치지 않고 바로
    조건을 만족하는 경우) 곧장 완료되지 않고, 이 일화에 더 하고 싶은 이야기가
    있는지 한 번 확인해야 한다(2026-07-15 피드백)."""
    _, token = _signup_and_login(client, "wrapup-a@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        turn = _send_message(client, token, session["id"], _RICH_ANSWER)
    assert turn.status_code == 200
    body = turn.json()
    assert body["session"]["status"] == "open"
    assert body["assistant_message"]["content"] == prompts.WRAP_UP_CHECK_IN_MESSAGE


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


def test_next_preview_shows_first_question_before_any_session_exists(client: TestClient) -> None:
    """대화창을 열자마자(세션을 만들기 전) 다음 질문이 무엇인지 알 수 있어야 한다
    (2026-07-14 프론트 실사용 중 발견 — "어떤 대화를 해볼까요?" 같은 정적 문구 대신
    실제 다음 질문이 인사말에 담겨야 함)."""
    _, token = _signup_and_login(client, "preview-a@example.com")
    first_question = next(q for q in default_store.questions.values() if q.sequence_order == 1)

    resp = client.get("/api/v1/interview-sessions/next-preview", headers=_auth_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_type"] == "fixed_question"
    assert first_question.content in body["opening_message"]
    # 미리보기만으로는 세션이 실제로 생성되지 않아야 한다(빈 세션 방지).
    assert client.get("/api/v1/interview-sessions", headers=_auth_headers(token)).json() == []


def test_next_preview_advances_after_a_question_is_completed(client: TestClient) -> None:
    _, token = _signup_and_login(client, "preview-b@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        _complete_session_via_chat(client, token, session["id"])

    second_question = next(q for q in default_store.questions.values() if q.sequence_order == 2)
    resp = client.get("/api/v1/interview-sessions/next-preview", headers=_auth_headers(token))
    assert second_question.content in resp.json()["opening_message"]


def test_sending_a_message_to_an_already_completed_session_is_rejected(client: TestClient) -> None:
    """프론트가 세션 완료 후 새 세션으로 넘어가지 않고 같은 session_id로 계속
    보내면(2026-07-14 재현된 버그), 매 턴마다 완료 처리·Phase 2 후처리가 중복
    실행돼 이벤트가 턴마다 중복 생성되는 문제가 있었다 — 서버가 이를 409로
    명확히 거부해 프론트가 반드시 새 세션을 만들도록 강제해야 한다."""
    _, token = _signup_and_login(client, "reject-a@example.com")
    session = client.post(
        "/api/v1/interview-sessions",
        json={"session_type": "fixed_question"},
        headers=_auth_headers(token),
    ).json()

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        completed_turn = _complete_session_via_chat(client, token, session["id"])
        assert completed_turn.json()["session"]["status"] == "completed"

        second_turn = client.post(
            f"/api/v1/interview-sessions/{session['id']}/messages",
            json={"content": "같은 세션에 계속 말해봅니다."},
            headers=_auth_headers(token),
        )
    assert second_turn.status_code == 409
