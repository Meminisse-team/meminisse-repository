"""_run_groundedness_check(Phase 4 근거 검증) — Solar LLM 판정 + groundedness-check
API 2차 게이트 회귀 테스트.

배경: 로컬 NLI(mDeBERTa) entailment로 문장 단위 대조를 했더니 감각적 묘사·
내적 성찰 같은 정당한 정교화까지 거의 전부 플래그되는 도구 부적합 문제와
챕터 하나에 20분 넘게 걸리는 속도 문제가 겹쳐 Solar LLM 판정(단일
structured_completion)으로 교체했다(2026-07-17). 이때 "애매하면 무조건 통과"
기준을 썼더니 명백한 날조까지 통과하는 recall 붕괴가 와서, 비대칭 기준 +
reasoning_effort="medium" 판정으로 강화하고, 플래그된 문장만 Upstage 전용
groundedness-check 모델로 2차 확인해 "grounded" 확정 시 철회하는 이중 게이트를
얹었다(같은 날).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.autobiography_service import _run_groundedness_check


class _FakeEventRecord:
    """EventRecord 대역 — 이 테스트는 prose_paragraph만 필요하다."""

    def __init__(self, prose_paragraph: str) -> None:
        self.prose_paragraph = prose_paragraph


def _make_gc_check(verdict: str, calls: list[dict] | None = None):
    async def _fake_check(*, context: str, answer: str) -> str:
        if calls is not None:
            calls.append({"context": context, "answer": answer})
        return verdict

    return _fake_check


@pytest.mark.asyncio
async def test_groundedness_check_calls_solar_once_with_chapter_and_sources() -> None:
    """플래그가 없으면 근거검증 전체가 단일 Solar 호출이어야 한다 — 문장/사건 수와
    무관하게 호출 횟수가 상수(1)여야, 예전의 NLI 순차/그룹 호출 방식으로
    되돌아가지 않았는지 확인할 수 있다. 2차 게이트(groundedness-check API)는
    플래그가 있을 때만 돌므로 이 경우 호출 0회여야 한다."""
    events = [_FakeEventRecord(f"사건 {i} 문단입니다.") for i in range(20)]
    call_count = {"n": 0}
    gc_calls: list[dict] = []
    captured_messages: list[list[dict]] = []

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        call_count["n"] += 1
        captured_messages.append(messages)
        assert schema_name == "groundedness_judge"
        return {"flags": []}

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_make_gc_check("grounded", gc_calls)),
    ):
        report = await _run_groundedness_check("본문 문장입니다.", source_events=events)

    assert call_count["n"] == 1
    assert gc_calls == []  # 플래그 없음 → 2차 게이트 생략(비용 낭비 방지)
    assert report["checked"] is True
    assert report["flags"] == []
    assert report["source_event_count"] == 20
    user_content = captured_messages[0][1]["content"]
    assert "본문 문장입니다." in user_content
    assert "사건 0 문단입니다." in user_content
    assert "사건 19 문단입니다." in user_content


@pytest.mark.asyncio
async def test_groundedness_check_keeps_flag_when_api_says_not_grounded() -> None:
    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {"flags": [{"sentence": "지어낸 문장입니다.", "reason": "근거 사건에 없는 새로운 인물 등장"}]}

    gc_calls: list[dict] = []
    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_make_gc_check("notGrounded", gc_calls)),
    ):
        report = await _run_groundedness_check(
            "지어낸 문장입니다.", source_events=[_FakeEventRecord("실제 사건 문단.")]
        )

    assert len(report["flags"]) == 1
    assert report["flags"][0]["sentence"] == "지어낸 문장입니다."
    assert report["flags"][0]["reason"] == "근거 사건에 없는 새로운 인물 등장"
    assert report["dismissed_by_groundedness_api"] == 0
    # 2차 게이트에는 근거 컨텍스트와 플래그된 문장이 그대로 전달되어야 한다.
    assert gc_calls == [{"context": "- 실제 사건 문단.", "answer": "지어낸 문장입니다."}]


@pytest.mark.asyncio
async def test_groundedness_check_dismisses_flag_when_api_confirms_grounded() -> None:
    """판정자(solar-pro3)가 오탐한 문장을 전용 모델이 "grounded"로 확정하면
    플래그를 철회해야 한다 — 비대칭 기준으로 강화된 판정자의 오탐 비용을 낮추는
    안전판."""

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {"flags": [{"sentence": "부산에서 태어났다.", "reason": "근거 확인 필요"}]}

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_make_gc_check("grounded")),
    ):
        report = await _run_groundedness_check(
            "부산에서 태어났다.", source_events=[_FakeEventRecord("나는 부산에서 태어났다.")]
        )

    assert report["flags"] == []
    assert report["dismissed_by_groundedness_api"] == 1


@pytest.mark.asyncio
async def test_groundedness_check_keeps_flag_when_api_call_fails() -> None:
    """2차 게이트 호출이 실패하면 플래그를 유지해야 한다 — 검증 실패가 검증
    통과로 둔갑하면 안 된다(보수적 처리)."""

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {"flags": [{"sentence": "지어낸 문장입니다.", "reason": "근거 없음"}]}

    async def _failing_check(*, context: str, answer: str) -> str:
        raise RuntimeError("Upstage API down")

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_failing_check),
    ):
        report = await _run_groundedness_check(
            "지어낸 문장입니다.", source_events=[_FakeEventRecord("실제 사건 문단.")]
        )

    assert len(report["flags"]) == 1
    assert report["dismissed_by_groundedness_api"] == 0


@pytest.mark.asyncio
async def test_groundedness_check_passes_attention_paragraphs_to_judge() -> None:
    """근거 태그 없이 집필된 문단(_strip_citation_tags가 수집)은 판정자 프롬프트에
    '특히 주의해서 검토' 대상으로 지목되어야 한다."""
    captured_messages: list[list[dict]] = []

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        captured_messages.append(messages)
        return {"flags": []}

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        await _run_groundedness_check(
            "본문 문장입니다.",
            source_events=[_FakeEventRecord("사건 문단.")],
            attention_paragraphs=["태그 없는 문단입니다."],
        )

    user_content = captured_messages[0][1]["content"]
    assert "특히 주의해서 검토" in user_content
    assert "태그 없는 문단입니다." in user_content


@pytest.mark.asyncio
async def test_groundedness_check_returns_unchecked_when_no_source_events() -> None:
    """근거 사건이 없으면 Solar 호출 자체를 생략해야 한다(비용 낭비 방지)."""
    called = {"n": 0}

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        called["n"] += 1
        return {"flags": []}

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        report = await _run_groundedness_check("문장입니다.", source_events=[])

    assert called["n"] == 0
    assert report["checked"] is False
    assert report["flags"] == []


@pytest.mark.asyncio
async def test_groundedness_check_returns_unchecked_when_content_empty() -> None:
    called = {"n": 0}

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        called["n"] += 1
        return {"flags": []}

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        report = await _run_groundedness_check("   ", source_events=[_FakeEventRecord("사건.")])

    assert called["n"] == 0
    assert report["checked"] is False
