"""evals/baseline_and_ablations.py의 순수 로직(이벤트 병합) 회귀 테스트. 실제 조건
생성(Solar/DB 호출을 거치는 run_* 함수들)은 evals/README.md에 문서화된 스크립트
실행으로 검증한다."""

from __future__ import annotations

from evals.baseline_and_ablations import _merge_extracted_events


def test_merge_single_event_returns_it_unchanged() -> None:
    event = {"source_type": "session_chat", "one_line_summary": "요약", "prose_paragraph": "문단"}
    assert _merge_extracted_events([event]) is event


def test_merge_concatenates_prose_and_summaries() -> None:
    events = [
        {"source_type": "session_chat", "one_line_summary": "부산 이주", "prose_paragraph": "우리 가족은 부산으로 이사했다."},
        {"source_type": "session_chat", "one_line_summary": "새 학교 적응", "prose_paragraph": "나는 새 학교에 적응해야 했다."},
    ]
    merged = _merge_extracted_events(events)
    assert "부산 이주" in merged["one_line_summary"]
    assert "새 학교 적응" in merged["one_line_summary"]
    assert "부산으로 이사했다" in merged["prose_paragraph"]
    assert "새 학교에 적응해야 했다" in merged["prose_paragraph"]


def test_merge_takes_first_non_null_for_scalar_fields() -> None:
    events = [
        {"source_type": "session_chat", "one_line_summary": "a", "prose_paragraph": "a", "place": None, "occurred_at_label": "1975년"},
        {"source_type": "session_chat", "one_line_summary": "b", "prose_paragraph": "b", "place": "부산", "occurred_at_label": "1976년"},
    ]
    merged = _merge_extracted_events(events)
    assert merged["place"] == "부산"  # 첫 이벤트엔 없어서 두 번째 값을 채택
    assert merged["occurred_at_label"] == "1975년"  # 첫 이벤트에 이미 있으므로 그대로


def test_merge_unions_labels_preferring_first_non_null() -> None:
    events = [
        {"source_type": "session_chat", "one_line_summary": "a", "prose_paragraph": "a", "labels": {"regret": "후회했다"}},
        {"source_type": "session_chat", "one_line_summary": "b", "prose_paragraph": "b", "labels": {"regret": "다른 후회", "pride": "자부심"}},
    ]
    merged = _merge_extracted_events(events)
    assert merged["labels"]["regret"] == "후회했다"  # 첫 이벤트 값 우선
    assert merged["labels"]["pride"] == "자부심"  # 첫 이벤트엔 없던 키는 채택


def test_merge_takes_max_emotion_intensity() -> None:
    events = [
        {"source_type": "session_chat", "one_line_summary": "a", "prose_paragraph": "a", "emotion_intensity": 3},
        {"source_type": "session_chat", "one_line_summary": "b", "prose_paragraph": "b", "emotion_intensity": 7},
    ]
    merged = _merge_extracted_events(events)
    assert merged["emotion_intensity"] == 7
