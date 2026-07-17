"""_run_groundedness_check(Phase 4 근거 검증) — 로컬 NLI에서 Solar LLM 판정으로
교체한 회귀 테스트.

배경: 로컬 NLI(mDeBERTa) entailment로 문장 단위 대조를 했더니 감각적 묘사·
내적 성찰 같은 정당한 정교화까지 "원문을 논리적으로 함의하지 않는다"는
이유로 거의 전부 플래그되는 근본적인 도구 부적합 문제가 있었고, 512 토큰
제약 때문에 그룹핑을 해도 챕터 하나에 20분 넘게 걸렸다(2026-07-17). 같은
목적(챕터 본문을 근거 자료와 대조)을 이미 쓰고 있는 _run_factcheck와
동일한 패턴(단일 solar.structured_completion 호출)으로 교체했다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.autobiography_service import _run_groundedness_check


class _FakeEventRecord:
    """EventRecord 대역 — 이 테스트는 prose_paragraph만 필요하다."""

    def __init__(self, prose_paragraph: str) -> None:
        self.prose_paragraph = prose_paragraph


@pytest.mark.asyncio
async def test_groundedness_check_calls_solar_once_with_chapter_and_sources() -> None:
    """근거검증 전체가 단일 Solar 호출이어야 한다 — 문장/사건 수와 무관하게
    호출 횟수가 상수(1)여야, 예전의 NLI 순차/그룹 호출 방식으로 되돌아가지
    않았는지 확인할 수 있다."""
    events = [_FakeEventRecord(f"사건 {i} 문단입니다.") for i in range(20)]
    call_count = {"n": 0}
    captured_messages: list[list[dict]] = []

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        call_count["n"] += 1
        captured_messages.append(messages)
        assert schema_name == "groundedness_judge"
        return {"flags": []}

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        report = await _run_groundedness_check("본문 문장입니다.", source_events=events)

    assert call_count["n"] == 1
    assert report["checked"] is True
    assert report["flags"] == []
    assert report["source_event_count"] == 20
    user_content = captured_messages[0][1]["content"]
    assert "본문 문장입니다." in user_content
    assert "사건 0 문단입니다." in user_content
    assert "사건 19 문단입니다." in user_content


@pytest.mark.asyncio
async def test_groundedness_check_surfaces_llm_flags() -> None:
    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        return {"flags": [{"sentence": "지어낸 문장입니다.", "reason": "근거 사건에 없는 새로운 인물 등장"}]}

    with patch("app.clients.solar.structured_completion", new=_fake_structured_completion):
        report = await _run_groundedness_check(
            "지어낸 문장입니다.", source_events=[_FakeEventRecord("실제 사건 문단.")]
        )

    assert len(report["flags"]) == 1
    assert report["flags"][0]["sentence"] == "지어낸 문장입니다."
    assert report["flags"][0]["reason"] == "근거 사건에 없는 새로운 인물 등장"


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
