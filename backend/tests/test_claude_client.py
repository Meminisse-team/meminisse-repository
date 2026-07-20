"""app/clients/claude.py 회귀 테스트 — 프롬프트 캐싱과 low-effort max_tokens
안전판(2026-07-20)을 검증한다. 실제 Anthropic API를 호출하지 않고 client.messages.
stream을 가짜로 대체해 어떤 kwargs로 호출됐는지만 확인한다(이 환경엔
ANTHROPIC_API_KEY가 설정돼 있지 않아 실제 호출이 애초에 불가능하다)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from app.clients import claude


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeMessage:
    content: list[_FakeTextBlock] = field(default_factory=lambda: [_FakeTextBlock("응답")])


class _FakeStream:
    async def get_final_message(self) -> _FakeMessage:
        return _FakeMessage()


class _FakeMessagesNamespace:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    @asynccontextmanager
    async def stream(self, **kwargs: Any):
        self.last_kwargs = kwargs
        yield _FakeStream()


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessagesNamespace()


@pytest.mark.asyncio
async def test_system_prompt_gets_ephemeral_cache_control() -> None:
    """system 메시지는 챕터마다 재사용되므로 cache_control이 붙어야 한다."""
    fake_client = _FakeClient()
    with patch("app.clients.claude.get_claude_client", return_value=fake_client):
        await claude.chat_completion(
            [
                {"role": "system", "content": "고정 시스템 프롬프트"},
                {"role": "user", "content": "이번 챕터 내용"},
            ]
        )

    assert fake_client.messages.last_kwargs is not None
    system = fake_client.messages.last_kwargs["system"]
    assert system == [
        {"type": "text", "text": "고정 시스템 프롬프트", "cache_control": {"type": "ephemeral"}}
    ]


@pytest.mark.asyncio
async def test_low_effort_call_gets_smaller_max_tokens_safety_cap() -> None:
    """reasoning_effort='low'인데 호출부가 max_tokens를 안 줬으면, 폭주 방지용으로
    _DEFAULT_MAX_TOKENS(32000)이 아니라 훨씬 작은 안전판을 써야 한다."""
    fake_client = _FakeClient()
    with patch("app.clients.claude.get_claude_client", return_value=fake_client):
        await claude.chat_completion(
            [{"role": "user", "content": "짧은 판정 요청"}], reasoning_effort="low"
        )

    assert fake_client.messages.last_kwargs["max_tokens"] == claude._LOW_EFFORT_DEFAULT_MAX_TOKENS
    assert claude._LOW_EFFORT_DEFAULT_MAX_TOKENS < claude._DEFAULT_MAX_TOKENS


@pytest.mark.asyncio
async def test_explicit_max_tokens_overrides_low_effort_default() -> None:
    """호출부가 max_tokens를 명시하면 low-effort 안전판보다 그 값이 우선해야 한다."""
    fake_client = _FakeClient()
    with patch("app.clients.claude.get_claude_client", return_value=fake_client):
        await claude.chat_completion(
            [{"role": "user", "content": "내용"}], reasoning_effort="low", max_tokens=999
        )

    assert fake_client.messages.last_kwargs["max_tokens"] == 999


@pytest.mark.asyncio
async def test_medium_effort_call_keeps_default_max_tokens() -> None:
    """low가 아닌 effort는 기존 _DEFAULT_MAX_TOKENS(32000)를 그대로 써야 한다 —
    챕터 집필처럼 긴 출력이 필요한 호출이 이 안전판에 영향받으면 안 된다."""
    fake_client = _FakeClient()
    with patch("app.clients.claude.get_claude_client", return_value=fake_client):
        await claude.chat_completion(
            [{"role": "user", "content": "내용"}], reasoning_effort="medium"
        )

    assert fake_client.messages.last_kwargs["max_tokens"] == claude._DEFAULT_MAX_TOKENS
