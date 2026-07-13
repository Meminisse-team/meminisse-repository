"""
evals/deepeval_narrative_coherence.py의 판정 LLM과 무관한 배선 로직 회귀 테스트.

GEval/SolarJudgeModel의 실제 판정 호출은 evals/README.md에 문서화된 스크립트 실행
(실제 Solar API)으로 검증하지, 여기서는 판정 자체를 모킹하고 "JSON 결과가 Mock
게이트웨이의 올바른 이벤트로 재구성되는가"와 "GEval 결과가 우리 리포트 스키마로
올바르게 매핑되는가"만 확인한다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.gateways.mock.store import default_store
from evals.deepeval_narrative_coherence import _reconstruct_persona_gateways, _score_narrative_coherence
from evals.solar_judge_model import SolarJudgeModel

_PERSONA_RESULT = {
    "persona_id": "p_test",
    "persona_name": "테스터",
    "birth_year": 1955,
    "hometown": "부산",
    "session_prose": "그날은 눈이 많이 내리던 날이었다.",
    "extracted_events": [
        {
            "source_type": "session_chat",
            "occurred_at_label": "1963년 겨울",
            "place": "부산",
            "people": "어머니",
            "one_line_summary": "눈 오는 날 이사",
            "prose_paragraph": "그날은 눈이 많이 내리던 날이었다.",
            "emotion_tag": "불안",
            "emotion_intensity": 3,
            "emotion_inferred": False,
            "labels": {"values_reflected": "가족애"},
            "confidence": {"place": 1.0},
            "source_span": {"quoted_text": "그날은 눈이 많이 내리던 날이었다."},
            "life_period": "childhood",
        },
        {
            "source_type": "session_chat",
            "occurred_at_label": None,
            "place": None,
            "people": None,
            "one_line_summary": "부수 사건",
            "prose_paragraph": "부수 사건 내용.",
            "emotion_tag": None,
            "emotion_intensity": None,
            "emotion_inferred": False,
            "labels": {},
            "confidence": None,
            "source_span": None,
            "life_period": None,
        },
    ],
}


async def _fake_embed_passages(texts: list[str]) -> list[list[float]]:
    return [[0.0] for _ in texts]


@pytest.mark.asyncio
async def test_reconstruct_persona_gateways_creates_user_session_and_events() -> None:
    with patch("app.clients.embeddings.embed_passages", new=_fake_embed_passages):
        gateways, user = await _reconstruct_persona_gateways(_PERSONA_RESULT)

    assert user.name == "테스터"
    assert user.birth_year == 1955

    events = [e for e in default_store.events.values() if e.user_id == user.id]
    assert len(events) == 2
    first = next(e for e in events if e.one_line_summary == "눈 오는 날 이사")
    assert first.place == "부산"
    assert first.people == "어머니"
    assert first.labels["values_reflected"] == "가족애"
    assert first.life_period.value == "childhood"

    second = next(e for e in events if e.one_line_summary == "부수 사건")
    assert second.life_period is None
    assert second.place is None


@pytest.mark.asyncio
async def test_score_narrative_coherence_maps_geval_result_into_report_fields() -> None:
    class _FakeMetric:
        def __init__(self, *args, **kwargs) -> None:
            self.score = 0.87
            self.reason = "일관성 있음"
            self.success = True

        async def a_measure(self, test_case, **kwargs):
            return self.score

    with patch("evals.deepeval_narrative_coherence.GEval", new=_FakeMetric):
        judge = SolarJudgeModel.__new__(SolarJudgeModel)  # a_generate 호출 없이 인스턴스만 필요
        result = await _score_narrative_coherence(
            judge,
            {"title": "1장. 눈 오는 날", "book_synopsis": "어린 시절의 기억", "final_content": "완성된 원고 본문"},
        )

    assert result == {
        "title": "1장. 눈 오는 날",
        "score": 0.87,
        "reason": "일관성 있음",
        "success": True,
    }
