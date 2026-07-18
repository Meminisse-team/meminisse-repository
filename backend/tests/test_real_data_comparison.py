"""evals/real_data_comparison.py 및 evals/baseline_and_ablations.py의 실제 DB용
이벤트 병합 어댑터에 대한 순수 로직 회귀 테스트. 실제 Supabase Auth 계정 생성,
Postgres 접근, Solar 호출은 evals/real_data_comparison.py의 모듈 docstring에
문서화된 실행 절차로 검증한다(비용/부작용 — 계정 생성 포함이라 테스트에서 목업
없이 돌리면 안 됨)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.gateways.dto import EventRecord
from app.models.enums import EventSourceType, LifePeriod
from evals.baseline_and_ablations import _event_record_to_merge_dict, merge_event_records
from evals.real_data_comparison import _CONDITIONS, _shadow_email


def _make_event(**overrides) -> EventRecord:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source_type=EventSourceType.SESSION_CHAT,
        session_id=None,
        media_asset_id=None,
        source_span=None,
        life_period=LifePeriod.CHILDHOOD,
        occurred_at_label="1963년 겨울",
        place="부산",
        people="어머니",
        one_line_summary="부산에서 태어남",
        prose_paragraph="나는 1963년 겨울 부산에서 태어났다.",
        emotion_tag="평온",
        emotion_intensity=3,
        emotion_inferred=False,
        labels={"values_reflected": "가족애"},
        confidence=None,
        verified=True,
        is_must_include=False,
        embedding=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return EventRecord(**defaults)


def test_event_record_to_merge_dict_converts_enums_to_strings() -> None:
    event = _make_event()
    result = _event_record_to_merge_dict(event)
    assert result["source_type"] == "session_chat"
    assert result["life_period"] == "childhood"
    assert result["place"] == "부산"
    assert result["labels"] == {"values_reflected": "가족애"}


def test_event_record_to_merge_dict_handles_null_life_period() -> None:
    event = _make_event(life_period=None)
    result = _event_record_to_merge_dict(event)
    assert result["life_period"] is None


def test_merge_event_records_combines_multiple_real_events() -> None:
    events = [
        _make_event(one_line_summary="사건 A", prose_paragraph="문단 A"),
        _make_event(one_line_summary="사건 B", prose_paragraph="문단 B", place=None),
    ]
    merged = merge_event_records(events)
    assert "사건 A" in merged["one_line_summary"]
    assert "사건 B" in merged["one_line_summary"]
    assert "문단 A" in merged["prose_paragraph"]
    assert "문단 B" in merged["prose_paragraph"]
    assert merged["place"] == "부산"  # 두 번째 이벤트엔 없어 첫 이벤트 값 채택


def test_merge_event_records_single_event_passthrough() -> None:
    event = _make_event()
    merged = merge_event_records([event])
    assert merged["one_line_summary"] == "부산에서 태어남"


def test_shadow_email_preserves_local_and_domain_with_suffix() -> None:
    email = _shadow_email("billgates@example.com", suffix="no-dynamic-toc")
    local, _, domain = email.partition("@")
    assert domain == "example.com"
    assert local.startswith("billgates+no-dynamic-toc-")


def test_shadow_email_is_unique_across_calls() -> None:
    email1 = _shadow_email("billgates@example.com", suffix="no-event-split")
    email2 = _shadow_email("billgates@example.com", suffix="no-event-split")
    assert email1 != email2


def test_conditions_include_with_followup() -> None:
    # 2026-07-18 추가 — Test A(꼬리질문 유무 비교)를 real-data 경로에도 적용.
    assert "with_followup" in _CONDITIONS
    assert _CONDITIONS.index("with_followup") == len(_CONDITIONS) - 1
