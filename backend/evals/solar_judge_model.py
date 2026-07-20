"""
DeepEval(evals/README.md 2·3절)의 LLM-as-judge 메트릭이 기본으로는 OpenAI를 판정
LLM으로 쓴다. 이 프로젝트엔 OpenAI API 키가 없고 Upstage Solar 키만 있으므로,
`DeepEvalBaseLLM`을 상속해 app/clients/solar.py(실제 서비스가 쓰는 것과 동일한
클라이언트)를 감싼 판정 모델을 만든다(2026-07-12 결정 — evals/README.md 2절 참조).

라벨추출 정확도 스크립트(deepeval_label_accuracy.py)와 서사일관성 스크립트
(deepeval_narrative_coherence.py) 양쪽이 이 모델을 공유한다.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from deepeval.models.base_model import DeepEvalBaseLLM
from pydantic import BaseModel

from app.clients import solar

T = TypeVar("T", bound=BaseModel)

JUDGE_MODEL_NAME = "solar-pro3"


def pydantic_schema_to_upstage_json_schema(schema_cls: type[BaseModel]) -> dict[str, Any]:
    """Upstage Structured Outputs 제약(additionalProperties=false, 모든 필드가
    required에 포함되어야 함 — app/agents/prompts.py EVENT_EXTRACTION_SCHEMA
    상단 주석 참조)에 맞게 pydantic model_json_schema() 출력을 손질한다.

    이 프로젝트에서 실제로 넘어오는 스키마(GEval의 ReasonScore/Steps, 아래
    SlotJudgement)는 얕은(중첩 없는) 구조라 최상위 보정만으로 충분하다."""
    raw = schema_cls.model_json_schema()
    raw["additionalProperties"] = False
    raw["required"] = list(raw.get("properties", {}).keys())
    return raw


class SolarJudgeModel(DeepEvalBaseLLM):
    """DeepEval 메트릭(GEval 등)이 판정 LLM으로 쓰는 커스텀 모델. schema가 주어지면
    Solar의 Structured Outputs로, 없으면 일반 chat_completion으로 응답한다."""

    def load_model(self) -> "SolarJudgeModel":
        return self

    def get_model_name(self) -> str:
        return JUDGE_MODEL_NAME

    def generate(self, prompt: str, schema: type[T] | None = None) -> str | T:
        return asyncio.run(self.a_generate(prompt, schema=schema))

    async def a_generate(self, prompt: str, schema: type[T] | None = None) -> str | T:
        if schema is None:
            response = await solar.chat_completion(
                [{"role": "user", "content": prompt}], reasoning_effort="high"
            )
            return response.choices[0].message.content or ""

        json_schema = pydantic_schema_to_upstage_json_schema(schema)
        result = await solar.structured_completion(
            [{"role": "user", "content": prompt}],
            schema_name=schema.__name__.lower(),
            json_schema=json_schema,
            reasoning_effort="high",
        )
        return schema(**result)
