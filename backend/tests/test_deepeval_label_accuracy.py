"""
evals/deepeval_label_accuracy.py의 순수 로직(judge LLM 호출과 무관한 부분) 회귀 테스트.

judge 호출 자체(SolarJudgeModel을 통한 실제 Solar API 호출)는 evals/README.md에
문서화된 스크립트 실행으로 검증하지, 여기서는 모킹하지 않는다(비용/속도) — 여기서는
슬롯 추출·집계 로직만 검증한다.
"""

from __future__ import annotations

from evals.deepeval_label_accuracy import _SLOT_EXTRACTORS, _aggregate
from evals.solar_judge_model import pydantic_schema_to_upstage_json_schema


def test_slot_extractors_pull_correct_fields_from_event_dict() -> None:
    event = {
        "place": "부산",
        "occurred_at_label": "1963년 겨울",
        "emotion_tag": "불안",
        "people": "어머니",
        "labels": {
            "values_reflected": "가족애",
            "gratitude": "어머니께 감사",
            "regret": None,
        },
    }
    assert _SLOT_EXTRACTORS["place"](event) == "부산"
    assert _SLOT_EXTRACTORS["time"](event) == "1963년 겨울"
    assert _SLOT_EXTRACTORS["emotion"](event) == "불안"
    assert _SLOT_EXTRACTORS["companion"](event) == "어머니"
    assert _SLOT_EXTRACTORS["values"](event) == "가족애"
    assert _SLOT_EXTRACTORS["gratitude"](event) == "어머니께 감사"
    assert _SLOT_EXTRACTORS["regret"](event) is None


def test_slot_extractors_handle_missing_labels_dict_gracefully() -> None:
    event = {"place": None, "people": None}
    assert _SLOT_EXTRACTORS["values"](event) is None
    assert _SLOT_EXTRACTORS["gratitude"](event) is None


def test_aggregate_computes_recall_and_precision_across_personas() -> None:
    reports = {
        "p01": {
            "place": {
                "ground_truth": "부산",
                "extracted_values": ["부산", "서울"],
                "ground_truth_captured": True,
                "all_extracted_values_grounded": False,
            },
            "emotion": {
                "ground_truth": "불안",
                "extracted_values": [],
                "ground_truth_captured": False,
                "all_extracted_values_grounded": True,
            },
        },
        "p02": {
            "place": {
                "ground_truth": "목포",
                "extracted_values": ["목포"],
                "ground_truth_captured": True,
                "all_extracted_values_grounded": True,
            },
        },
    }
    summary = _aggregate(reports)

    assert summary["place"]["recall"] == 1.0  # 2/2 captured
    assert summary["place"]["precision"] == 0.5  # 1/2 fully grounded
    assert summary["emotion"]["recall"] == 0.0  # 0/1 captured
    # extracted_values가 비어 있으면 precision 분모에서 제외한다(판단 대상 자체가 없음).
    assert summary["emotion"]["precision"] is None


def test_aggregate_handles_no_data_without_dividing_by_zero() -> None:
    assert _aggregate({}) == {}


def test_pydantic_schema_converter_marks_all_fields_required_and_no_additional_properties() -> None:
    from pydantic import BaseModel

    class Example(BaseModel):
        a: str
        b: bool

    schema = pydantic_schema_to_upstage_json_schema(Example)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"a", "b"}
