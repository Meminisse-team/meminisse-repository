"""
왜곡 탐지/수리(event_extraction_service._passes_distortion_check,
_repair_distorted_prose) 회귀 테스트.

로컬 NLI(mDeBERTa) 기반이었던 예전 버전은 세션 하나에 190~210초(로컬 CPU 추론)가
걸려 처리 파이프라인의 스테이지 타임아웃을 반복적으로 넘기는 문제가 있었다
(2026-07-19) — autobiography_service._run_groundedness_check가 겪었던 것과 같은
문제를 같은 방식(Solar LLM 판정으로 교체)으로 해소했으므로, 여기서는 실제 로컬
모델을 돌리지 않고 solar.chat_completion을 모킹해 판정/수리 로직만 검증한다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.event_extraction_service import (
    _DISTORTION_JUDGE_MODEL,
    _passes_distortion_check,
    _repair_distorted_prose,
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
        passed, reason = await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요."}],
            reassembled_prose="스무 살 때 혼자 부산으로 내려갔다.",
        )
    assert passed
    assert reason is None


@pytest.mark.asyncio
async def test_distortion_check_fails_when_judge_returns_fail() -> None:
    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion("FAIL: 원본에 없는 결혼 이야기가 새로 추가됨")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        passed, reason = await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요."}],
            reassembled_prose="나는 서른 살에 결혼해서 서울에서 신혼집을 차렸다.",
        )
    assert not passed
    assert reason == "FAIL: 원본에 없는 결혼 이야기가 새로 추가됨"


@pytest.mark.asyncio
async def test_distortion_check_fails_closed_on_off_protocol_response() -> None:
    """빈 응답이나 PASS/FAIL 어느 쪽도 아닌 응답은 검증 실패로 안전하게 처리한다
    (clients/groundedness.py와 동일한 "검증 실패가 검증 통과로 둔갑하면 안 된다"
    원칙)."""

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion("")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        passed, _reason = await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "원본 발화."}],
            reassembled_prose="재조립본.",
        )
    assert not passed


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
        passed, reason = await _passes_distortion_check(
            original_turns=[{"role": "assistant", "content": "질문만 있고 답변이 없음"}],
            reassembled_prose="",
        )
    assert passed
    assert reason is None


@pytest.mark.asyncio
async def test_repair_distorted_prose_uses_reassembly_model_not_mini() -> None:
    """수리는 판정(judge)이 아니라 집필(writer) 작업이므로, 자기선호 편향 우려가
    적용되는 solar-mini가 아니라 재조립과 같은 모델(기본값, solar-pro3)을 써야
    한다 — model 인자를 아예 넘기지 않아야 solar.chat_completion의 기본값이 적용된다."""
    captured: dict = {}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        captured["model"] = kwargs.get("model")
        return _FakeCompletion("수리된 산문.")

    turns = [{"role": "user", "content": "원본 발화."}]
    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        result = await _repair_distorted_prose(
            reassembled_prose="원래 산문.",
            chat_turns=turns,
            reassembly_turns=turns,
            fail_reason="FAIL: 지어낸 내용이 있음.",
        )

    assert "model" not in captured or captured["model"] is None
    assert result == "수리된 산문."


@pytest.mark.asyncio
async def test_repair_distorted_prose_strips_leaked_assistant_sentences() -> None:
    """수리 결과에도 재조립과 동일하게 assistant 턴 유출 필터를 적용해야 한다."""
    leaked_line = "다음 이야기로 넘어가 볼까요?"

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion(f"정상 문장. {leaked_line}")

    full_turns = [
        {"role": "assistant", "content": leaked_line},
        {"role": "user", "content": "정상 문장."},
    ]
    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        result = await _repair_distorted_prose(
            reassembled_prose="원래 산문.",
            chat_turns=full_turns,
            reassembly_turns=full_turns,
            fail_reason="FAIL: 사유.",
        )

    assert leaked_line not in result
    assert "정상 문장." in result


@pytest.mark.asyncio
async def test_repair_distorted_prose_uses_reassembly_turns_for_ground_truth() -> None:
    """"원본 발화" 근거는 reassembly_turns(마무리 확인 질문+답변 제외)로 만들어야
    한다 — chat_turns(전체)를 쓰면 마무리 답변("네, 이 이야기는 이 정도면...")이
    "원본에 있으니 지우지 말라"는 수리 지시 때문에 오히려 산문에 다시 끼어드는
    사고가 실사용 중 확인됐다(2026-07-19). chat_turns에는 마무리 답변이 있고
    reassembly_turns에는 없게 구성해, 프롬프트에 실제로 들어간 원문에 그 답변이
    없는지 확인한다."""
    wrap_up_reply = "네, 이 이야기는 이 정도면 충분히 말씀드린 것 같습니다."
    captured: dict = {}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        captured["user_message"] = messages[1]["content"]
        return _FakeCompletion("수리된 산문.")

    chat_turns = [
        {"role": "user", "content": "본 사건 발화."},
        {"role": "assistant", "content": "혹시 더 들려주고 싶은 게 있으신가요?"},
        {"role": "user", "content": wrap_up_reply},
    ]
    reassembly_turns = [{"role": "user", "content": "본 사건 발화."}]

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        await _repair_distorted_prose(
            reassembled_prose="원래 산문.",
            chat_turns=chat_turns,
            reassembly_turns=reassembly_turns,
            fail_reason="FAIL: 사유.",
        )

    assert wrap_up_reply not in captured["user_message"]
    assert "본 사건 발화." in captured["user_message"]
