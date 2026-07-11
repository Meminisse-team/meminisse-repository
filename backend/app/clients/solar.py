"""
Solar LLM (solar-pro3) 채팅 완성 클라이언트.

기획안 3절: 핀셋 질문 생성, 슬롯 게이팅, 이벤트 분할·라벨 추출, 세션 산문 재조립,
스타일 바이블·시놉시스·챕터 집필, 제3자 언급 위해성 분류 전 단계에서 이 모듈을 통해
Solar를 호출한다. 프롬프트 자체(페르소나/슬롯/세이프가드 문구)는 app/agents/prompts.py에서
관리하고, 이 모듈은 순수하게 "메시지 배열을 넣으면 응답을 받는" 얇은 API 래퍼로 유지한다.

NOTE(문서 자체 모순, 대안 구현함): upstage_solar_api_docs.txt 안에서 response_format
(Structured Outputs) 지원 모델 범위가 세 군데에서 서로 다르게 적혀 있다 —
(1) 파라미터 표: "all solar models (solar-pro3 포함)"
(2) Structured Outputs 예제 코드: model="solar-pro3"로 실제 동작하는 예시 제시
(3) response_format 필드 상세 설명: "only compatible with the solar-pro-2 model"
이벤트 추출 등 이 프로젝트의 핵심 파이프라인이 Structured Outputs에 의존하므로 실패를
문서 검증 시점까지 미룰 수 없어, structured_completion()에 자동 폴백을 구현했다: 우선
solar-pro3로 시도하고, API가 response_format 비호환 계열의 400 에러를 반환하면
solar-pro2로 1회 재시도한다. 실 UPSTAGE_API_KEY로 확인되면 이 폴백 분기가 영구히 타지
않는 죽은 코드가 될 수도 있으니, 검증 후 필요 없다고 판단되면 제거해도 된다.
"""

from __future__ import annotations

import json
from typing import Any

from openai import APIStatusError
from openai.types.chat import ChatCompletion

from app.clients.base import get_upstage_client

DEFAULT_MODEL = "solar-pro3"

# structured_completion()의 response_format 비호환 폴백 대상. 위 NOTE 참조.
STRUCTURED_OUTPUT_FALLBACK_MODEL = "solar-pro2"


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
    if response_format is not None:
        kwargs["response_format"] = response_format
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    # reasoning_effort/prompt_cache_key는 Upstage(Solar) API가 지원하는 필드지만,
    # 이 프로젝트에 고정된 openai SDK 버전(1.54.0)의 create()는 아직 이 두 파라미터를
    # 정식 인자로 알지 못해 그대로 넘기면 TypeError로 죽는다(실제 Solar 채팅 호출에서
    # 재현, 2026-07-11). SDK 버전과 무관하게 항상 존재하는 extra_body로 우회해 원시
    # 요청 바디에 실어 보낸다.
    extra_body: dict[str, Any] = {}
    if reasoning_effort is not None:
        extra_body["reasoning_effort"] = reasoning_effort
    if prompt_cache_key is not None:
        extra_body["prompt_cache_key"] = prompt_cache_key
    if extra_body:
        kwargs["extra_body"] = extra_body
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

    model이 solar-pro3(기본값)인데 API가 response_format을 이유로 400을 반환하면
    solar-pro2로 1회 폴백한다 — 모듈 상단 NOTE(문서 자체 모순) 참조.
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
    }
    try:
        response = await chat_completion(
            messages,
            model=model,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            prompt_cache_key=prompt_cache_key,
        )
    except APIStatusError as exc:
        if model != DEFAULT_MODEL or not _is_response_format_incompatibility(exc):
            raise
        response = await chat_completion(
            messages,
            model=STRUCTURED_OUTPUT_FALLBACK_MODEL,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            prompt_cache_key=prompt_cache_key,
        )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"Solar structured output '{schema_name}' returned empty content")
    return json.loads(content)


def _is_response_format_incompatibility(exc: APIStatusError) -> bool:
    if exc.status_code != 400:
        return False
    message = str(exc).lower()
    return "response_format" in message or "json_schema" in message
