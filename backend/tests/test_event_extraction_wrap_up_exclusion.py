"""
마무리 확인 질문(WRAP_UP_CHECK_IN_MESSAGE)과 그에 대한 사용자 응답이 산문에
새어 들어가는 버그의 회귀 테스트.

배경: 실사용 대화에서, 세션 마지막에 "혹시 이 이야기에 대해 더 들려주고 싶은 게
있으신가요? 없으면 다음 이야기로 넘어갈게요."라는 고정 질문에 사용자가 "넘어가자"
라고 답했는데, 이게 산문 재조립 결과에 "그 이야기를 마무리하며 다음으로 넘어가자."
라는 문장으로 그대로 섞여 들어갔다(2026-07-16). PROSE_REASSEMBLY_SYSTEM_PROMPT의
지시만으로는 못 미더우므로(_strip_leaked_assistant_sentences와 같은 이유),
event_extraction_service._exclude_wrap_up_exchange가 이 교환을 재조립 프롬프트에
넘기기 전에 코드 레벨에서 통째로 제외한다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.agents import prompts
from app.gateways.dto import SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import MessageRole, SessionType
from app.services import event_extraction_service
from app.services.event_extraction_service import _exclude_wrap_up_exchange


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def test_exclude_wrap_up_exchange_removes_confirmation_question_and_reply() -> None:
    chat_turns = [
        {"role": "assistant", "content": "질문"},
        {"role": "user", "content": "본 답변"},
        {"role": "assistant", "content": prompts.WRAP_UP_CHECK_IN_MESSAGE},
        {"role": "user", "content": "넘어가자"},
        {"role": "assistant", "content": "네, 잘 들었어요. 소중한 이야기 들려주셔서 감사해요."},
    ]

    filtered = _exclude_wrap_up_exchange(chat_turns)

    assert filtered == [
        {"role": "assistant", "content": "질문"},
        {"role": "user", "content": "본 답변"},
        {"role": "assistant", "content": "네, 잘 들었어요. 소중한 이야기 들려주셔서 감사해요."},
    ]


def test_exclude_wrap_up_exchange_keeps_turns_when_no_wrap_up_present() -> None:
    chat_turns = [
        {"role": "assistant", "content": "질문"},
        {"role": "user", "content": "답변"},
    ]

    assert _exclude_wrap_up_exchange(chat_turns) == chat_turns


@pytest.mark.asyncio
async def test_process_completed_session_never_sends_wrap_up_reply_to_reassembly_prompt() -> None:
    """실사용 재현: 마무리 확인 질문에 "넘어가자"라고 답한 세션을 완료 처리할 때,
    그 교환이 재조립 프롬프트(Solar 호출)에 아예 전달되지 않아야 한다 — 프롬프트가
    아무리 잘 지켜져도 애초에 입력에 없으면 새어 들어갈 수가 없다."""
    captured_messages: list = []

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        captured_messages.extend(messages)
        return _FakeCompletion("본 답변 내용.")

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        if schema_name == "distortion_check":
            # 왜곡 탐지(event_extraction_service._DISTORTION_JUDGE_MODEL) 호출 —
            # 이 테스트의 관심사가 아니므로 항상 통과시킨다.
            return {"flags": []}
        if schema_name == "event_extraction":
            return {"events": [], "relations": []}
        raise AssertionError(f"unexpected schema_name: {schema_name}")

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
    ):
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
        )
        await gateways.sessions.add_chat_log(
            session.id, role=MessageRole.ASSISTANT, content="질문"
        )
        await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content="본 답변 내용.")
        await gateways.sessions.add_chat_log(
            session.id, role=MessageRole.ASSISTANT, content=prompts.WRAP_UP_CHECK_IN_MESSAGE
        )
        await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content="넘어가자")
        await gateways.sessions.add_chat_log(
            session.id,
            role=MessageRole.ASSISTANT,
            content="네, 잘 들었어요. 소중한 이야기 들려주셔서 감사해요.",
        )
        await gateways.commit()

        await event_extraction_service.process_completed_session(gateways, session.id)

        reassembly_user_message = captured_messages[1]["content"]
        assert "넘어가자" not in reassembly_user_message
        assert prompts.WRAP_UP_CHECK_IN_MESSAGE not in reassembly_user_message
