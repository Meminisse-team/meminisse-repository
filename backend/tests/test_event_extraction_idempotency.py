"""
process_completed_session 멱등성 회귀 테스트.

배경: admin_service.reconcile_stale_sessions(5분마다 실행)가 "완료됐는데 아직
산문이 없는 세션"을 조건 없이 재큐잉한다 — 브로커 유실 복구용 안전망인데, 처리
대기열이 밀린 상황(대량 시딩 등)에서는 아직 처리 순서를 못 받은 세션까지 똑같이
다시 큐잉해버려 같은 세션이 여러 번 처리되고 이벤트가 중복 생성되는 사고가
실사용 중 재현됐다(2026-07-16, 세션 12개에서 확인). 이미 session_prose가 있는
세션에 대해서는 재조립·이벤트 추출을 다시 하지 않아야 한다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.gateways.dto import SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import MessageRole, SessionType
from app.services import event_extraction_service


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


@pytest.mark.asyncio
async def test_process_completed_session_skips_reassembly_when_prose_already_exists() -> None:
    """이미 session_prose가 있는 세션을 다시 처리 요청하면, Solar를 다시 호출하지
    않고 기존 이벤트만 그대로 반환해야 한다 - 재조립도, 이벤트 추가 생성도 없어야 함."""
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        call_count["n"] += 1
        return _FakeCompletion("이 함수가 다시 불리면 안 되므로 호출되면 테스트가 실패해야 한다.")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
        )
        await gateways.sessions.add_chat_log(
            session.id, role=MessageRole.ASSISTANT, content="질문 내용"
        )
        # 이미 재조립이 끝난 상태를 재현 - session_prose가 이미 채워져 있다.
        await gateways.sessions.set_session_prose(session.id, "이미 재조립된 산문.")
        await gateways.sessions.complete(session.id)
        await gateways.commit()

        events = await event_extraction_service.process_completed_session(gateways, session.id)

        assert events == []  # 이 세션은 애초에 이벤트를 추출한 적이 없으니 빈 리스트 그대로
        assert call_count["n"] == 0  # Solar 재조립 호출 자체가 아예 일어나지 않아야 한다
