"""
Solar LLM (solar-pro3) 채팅 완성 클라이언트.

기획안 3절: 핀셋 질문 생성, 슬롯 게이팅, 이벤트 분할·라벨 추출, 세션 산문 재조립,
스타일 바이블·시놉시스·챕터 집필, 제3자 언급 위해성 분류 전 단계에서 이 모듈을 통해
Solar를 호출한다. 프롬프트 자체(페르소나/슬롯/세이프가드 문구)는 app/agents/prompts.py에서
관리하고, 이 모듈은 순수하게 "메시지 배열을 넣으면 응답을 받는" 얇은 API 래퍼로 유지한다.

NOTE(검증 필요): upstage_solar_api_docs.txt 내에서 response_format(Structured Outputs)
지원 모델 범위가 문서 상단 가이드("solar-pro3 포함 전 모델 지원")와 하단 공식 스펙
("solar-pro-2 전용")으로 서로 모순된다. 이벤트 추출 파이프라인 전체가 Structured
Outputs에 의존하므로, 실제 UPSTAGE_API_KEY로 solar-pro3 + response_format 조합을 1회
테스트해 실패하면 STRUCTURED_OUTPUT_MODEL을 solar-pro2로 낮추는 폴백이 필요하다.
"""

from __future__ import annotations

import json
from typing import Any

from openai.types.chat import ChatCompletion

from app.clients.base import get_upstage_client

DEFAULT_MODEL = "solar-pro3"


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    prompt_cache_key: str | None = None,
) -> ChatCompletion:
    client = get_upstage_client()
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    if response_format is not None:
        kwargs["response_format"] = response_format
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    if prompt_cache_key is not None:
        kwargs["prompt_cache_key"] = prompt_cache_key
    return await client.chat.completions.create(**kwargs)


async def structured_completion(
    messages: list[dict[str, Any]],
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    model: str = DEFAULT_MODEL,
    reasoning_effort: str | None = "medium",
    prompt_cache_key: str | None = None,
) -> dict[str, Any]:
    """Structured Outputs(JSON 스키마 강제) 호출 후 파싱된 dict를 반환한다.

    schema는 기획안 3절 요구대로 "단일 라벨 딕셔너리"가 아닌 호출부에서 array-of-events
    형태로 구성해 전달하는 것을 전제로 한다(예: agents/prompts.py의 EVENT_EXTRACTION_SCHEMA).
    """
    response = await chat_completion(
        messages,
        model=model,
        reasoning_effort=reasoning_effort,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        },
        prompt_cache_key=prompt_cache_key,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"Solar structured output '{schema_name}' returned empty content")
    return json.loads(content)
