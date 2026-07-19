"""
왜곡 탐지/수리(event_extraction_service._passes_distortion_check,
_repair_distorted_prose) 회귀 테스트.

로컬 NLI(mDeBERTa) 기반이었던 예전 버전은 세션 하나에 190~210초(로컬 CPU 추론)가
걸려 처리 파이프라인의 스테이지 타임아웃을 반복적으로 넘기는 문제가 있었다
(2026-07-19) — autobiography_service._run_groundedness_check가 겪었던 것과 같은
문제를 같은 방식(Solar LLM 판정으로 교체)으로 해소했으므로, 여기서는 실제 로컬
모델을 돌리지 않고 solar.structured_completion/chat_completion을 모킹해
판정/수리 로직만 검증한다.

판정은 단문 PASS/FAIL 프로토콜에서 GROUNDEDNESS_JUDGE_SCHEMA와 동일한 문장 단위
flags 배열(Structured Outputs)로 바뀌었다(2026-07-19) — 산문 전체를 한 번에 보고
하나의 판정만 내리다 보니 오탐·미탐이 둘 다 실사용 중 확인됐고, solar-mini가 이
스키마를 실제로 지원함을 실측으로 확인했기 때문이다.

문장 단위로 바꾼 뒤에도 solar-mini가 원본에 그대로 있는 문장을 "지어냈다"고
확신에 차서 오판하는 사례가 재현돼(2026-07-19, 세션 911fbf5d), solar-pro3로
1차 판정을 재확인하는 2차 게이트(_confirm_distortion_flags)를 추가했다 —
autobiography_service._run_groundedness_check와 같은 패턴, clients/groundedness.py
재사용. 여기서는 groundedness.check도 함께 모킹해 2차 게이트 로직을 검증한다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.clients import groundedness
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
async def test_distortion_check_passes_when_judge_returns_no_flags() -> None:
    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        assert schema_name == "distortion_check"
        return {"flags": []}

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        passed, flags = await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요."}],
            reassembled_prose="스무 살 때 혼자 부산으로 내려갔다.",
        )
    assert passed
    assert flags == []


@pytest.mark.asyncio
async def test_distortion_check_fails_when_both_gates_agree() -> None:
    """1차(mini)가 플래그하고 2차(pro3, clients/groundedness.py)도 notGrounded로
    확인하면 최종 실패로 남아야 한다."""

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {
            "flags": [
                {"sentence": "나는 서른 살에 결혼했다.", "reason": "원본에 없는 결혼 이야기가 새로 추가됨"}
            ]
        }

    async def _fake_groundedness_check(*, context, answer, model=None):
        return groundedness.NOT_GROUNDED

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_check),
    ):
        passed, flags = await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "스무 살 때 혼자 부산으로 내려갔어요."}],
            reassembled_prose="나는 서른 살에 결혼했다.",
        )
    assert not passed
    assert flags == [
        {"sentence": "나는 서른 살에 결혼했다.", "reason": "원본에 없는 결혼 이야기가 새로 추가됨"}
    ]


@pytest.mark.asyncio
async def test_distortion_check_dismisses_false_positive_confirmed_grounded() -> None:
    """1차(mini)가 오탐으로 플래그해도, 2차(pro3)가 실제로 원본에 있다(grounded)고
    확인하면 최종 통과로 철회돼야 한다 — solar-mini가 원본에 그대로 있는 문장을
    "지어냈다"고 오판한 실사용 재현(세션 911fbf5d, 2026-07-19)에 대한 회귀 테스트."""

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {
            "flags": [
                {"sentence": "그때는 폴 앨런이랑 자주 부딪혔어요.", "reason": "원본에 없는 인물 언급"}
            ]
        }

    async def _fake_groundedness_check(*, context, answer, model=None):
        return groundedness.GROUNDED

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_check),
    ):
        passed, flags = await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "그때는 폴 앨런이랑 자주 부딪혔어요."}],
            reassembled_prose="그때는 폴 앨런이랑 자주 부딪혔어요.",
        )
    assert passed
    assert flags == []


@pytest.mark.asyncio
async def test_distortion_check_second_gate_fails_closed_on_error() -> None:
    """2차 게이트 호출이 실패해도(예외) 플래그를 유지해야 한다 — "검증 실패가
    검증 통과로 둔갑하면 안 된다"는 원칙은 clients/groundedness.py와 동일."""

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {"flags": [{"sentence": "지어낸 문장.", "reason": "사유"}]}

    async def _fake_groundedness_check_raises(*, context, answer, model=None):
        raise RuntimeError("네트워크 오류")

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_check_raises),
    ):
        passed, flags = await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "원본."}],
            reassembled_prose="지어낸 문장.",
        )
    assert not passed
    assert flags == [{"sentence": "지어낸 문장.", "reason": "사유"}]


@pytest.mark.asyncio
async def test_distortion_check_second_gate_uses_pro3_not_mini() -> None:
    """2차 게이트는 1차와 다른 모델(solar-pro3)을 써야 한다 — 1차 판정 모델(mini)이
    자기 오판을 또 확인하면 2차 게이트를 두는 의미가 없다."""
    captured: dict = {}

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {"flags": [{"sentence": "지어낸 문장.", "reason": "사유"}]}

    async def _fake_groundedness_check(*, context, answer, model=None):
        captured["model"] = model
        return groundedness.NOT_GROUNDED

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_check),
    ):
        await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "원본."}],
            reassembled_prose="지어낸 문장.",
        )

    assert captured["model"] == "solar-pro3"


@pytest.mark.asyncio
async def test_distortion_check_uses_mini_not_the_reassembly_model() -> None:
    """재조립을 생성하는 모델(solar-pro3)이 아니라 solar-mini로 판정해야 한다 —
    clients/groundedness.py의 실측(같은 계열 모델의 자기선호 편향)을 근거로 한
    설계 결정이므로, 실수로 되돌아가지 않도록 회귀 테스트로 고정한다."""
    captured: dict = {}

    async def _fake_structured_completion(messages, *, schema_name, json_schema, model=None, **kwargs):
        captured["model"] = model
        return {"flags": []}

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        await _passes_distortion_check(
            original_turns=[{"role": "user", "content": "원본 발화."}],
            reassembled_prose="재조립본.",
        )

    assert captured["model"] == _DISTORTION_JUDGE_MODEL == "solar-mini"


@pytest.mark.asyncio
async def test_distortion_check_skips_call_when_nothing_to_compare() -> None:
    """원문(사용자 발화)이나 재조립본이 비어 있으면 판정 자체가 불가능하므로
    Solar를 호출하지 않고 통과 처리한다."""

    async def _fail_if_called(messages, **kwargs):
        raise AssertionError("비교할 원문/재조립본이 없으면 Solar를 호출하면 안 된다")

    with patch("app.clients.solar.structured_completion", new=_fail_if_called):
        passed, flags = await _passes_distortion_check(
            original_turns=[{"role": "assistant", "content": "질문만 있고 답변이 없음"}],
            reassembled_prose="",
        )
    assert passed
    assert flags == []


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
            flags=[{"sentence": "원래 산문.", "reason": "지어낸 내용이 있음."}],
        )

    assert "model" not in captured or captured["model"] is None
    assert result == "수리된 산문."


@pytest.mark.asyncio
async def test_repair_distorted_prose_sends_flags_as_correction_targets() -> None:
    """flags 목록이 [수정 대상]으로 프롬프트에 그대로 실려야 한다 — 문장별로
    사유를 담아 정확히 어디를 고쳐야 하는지 수리 모델에 전달한다."""
    captured: dict = {}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        captured["user_message"] = messages[1]["content"]
        return _FakeCompletion("수리된 산문.")

    turns = [{"role": "user", "content": "원본 발화."}]
    flags = [
        {"sentence": "지어낸 문장 A.", "reason": "사유 A"},
        {"sentence": "지어낸 문장 B.", "reason": "사유 B"},
    ]
    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        await _repair_distorted_prose(
            reassembled_prose="원래 산문.",
            chat_turns=turns,
            reassembly_turns=turns,
            flags=flags,
        )

    assert "지어낸 문장 A." in captured["user_message"]
    assert "사유 A" in captured["user_message"]
    assert "지어낸 문장 B." in captured["user_message"]
    assert "사유 B" in captured["user_message"]


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
            flags=[{"sentence": "사유.", "reason": "사유."}],
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
            flags=[{"sentence": "사유.", "reason": "사유."}],
        )

    assert wrap_up_reply not in captured["user_message"]
    assert "본 사건 발화." in captured["user_message"]
