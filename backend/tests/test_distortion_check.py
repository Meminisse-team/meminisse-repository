"""
왜곡 탐지(event_extraction_service._passes_distortion_check) 회귀 테스트.

로컬 NLI(mDeBERTa) 기반이었던 예전 버전은 세션 하나에 190~210초(로컬 CPU 추론)가
걸려 처리 파이프라인의 스테이지 타임아웃을 반복적으로 넘기는 문제가 있었다
(2026-07-19) — autobiography_service._run_groundedness_check가 겪었던 것과 같은
문제를 같은 방식(Solar LLM 판정으로 교체)으로 해소했으므로, 여기서는 실제 로컬
모델을 돌리지 않고 solar.chat_completion을 모킹해 판정 로직(PASS/FAIL 프로토콜
해석)만 검증한다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.event_extraction_service import (
    _DISTORTION_JUDGE_MODEL,
    _passes_distortion_check,
)


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
async def test_distortion_check_passes_when_judge_returns_pass() -> None:
    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion("PASS")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        assert await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요."}],
            reassembled_prose="스무 살 때 혼자 부산으로 내려갔다.",
        )


@pytest.mark.asyncio
async def test_distortion_check_fails_when_judge_returns_fail() -> None:
    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion("FAIL: 원본에 없는 결혼 이야기가 새로 추가됨")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        assert not await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요."}],
            reassembled_prose="나는 서른 살에 결혼해서 서울에서 신혼집을 차렸다.",
        )


@pytest.mark.asyncio
async def test_distortion_check_fails_closed_on_off_protocol_response() -> None:
    """빈 응답이나 PASS/FAIL 어느 쪽도 아닌 응답은 검증 실패로 안전하게 처리한다
    (clients/groundedness.py와 동일한 "검증 실패가 검증 통과로 둔갑하면 안 된다"
    원칙)."""

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion("")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        assert not await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "원본 발화."}],
            reassembled_prose="재조립본.",
        )


@pytest.mark.asyncio
async def test_distortion_check_uses_mini_not_the_reassembly_model() -> None:
    """재조립을 생성하는 모델(solar-pro3)이 아니라 solar-mini로 판정해야 한다 —
    clients/groundedness.py의 실측(같은 계열 모델의 자기선호 편향)을 근거로 한
    설계 결정이므로, 실수로 되돌아가지 않도록 회귀 테스트로 고정한다."""
    captured: dict = {}

    async def _fake_chat_completion(messages, *, model=None, **kwargs) -> _FakeCompletion:
        captured["model"] = model
        return _FakeCompletion("PASS")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "원본 발화."}],
            reassembled_prose="재조립본.",
        )

    assert captured["model"] == _DISTORTION_JUDGE_MODEL == "solar-mini"


@pytest.mark.asyncio
async def test_distortion_check_skips_call_when_nothing_to_compare() -> None:
    """원문(사용자 발화)이나 재조립본이 비어 있으면 판정 자체가 불가능하므로
    Solar를 호출하지 않고 통과 처리한다."""

    async def _fail_if_called(messages, **kwargs) -> _FakeCompletion:
        raise AssertionError("비교할 원문/재조립본이 없으면 Solar를 호출하면 안 된다")

    with patch("app.clients.solar.chat_completion", new=_fail_if_called):
        assert await _passes_distortion_check(
            original_turns=[{"role": "assistant", "content": "질문만 있고 답변이 없음"}],
            reassembled_prose="",
        )
