"""
Solar LLM (solar-pro3) 채팅 완성 클라이언트.

기획안 3절: 핀셋 질문 생성, 슬롯 게이팅, 이벤트 분할·라벨 추출, 세션 산문 재조립,
스타일 바이블·시놉시스·챕터 집필, 제3자 언급 위해성 분류 전 단계에서 이 모듈을 통해
Solar를 호출한다. 프롬프트 자체(페르소나/슬롯/세이프가드 문구)는 app/agents/prompts/에서
관리하고, 이 모듈은 순수하게 "메시지 배열을 넣으면 응답을 받는" 얇은 API 래퍼로 유지한다.

Structured Outputs는 solar-pro3의 response_format을 사용하며 다른 모델로 자동 폴백하지 않는다.
공식 API 문서: https://console.upstage.ai/docs
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
    timeout: float | None = None,
) -> ChatCompletion:
    """timeout: 이 호출 하나에만 적용할 타임아웃(초). 생략하면 클라이언트
    기본값(90초, get_upstage_client 참조)을 그대로 쓴다. 대부분의 호출은
    기본값으로 충분하지만, 입출력이 유독 큰 소수의 호출(예:
    autobiography_service.finalize_manuscript의 배치별 통일성 윤문)은 이
    파라미터로 개별적으로 늘려준다 — 다른 모든 호출을 보호하는 90초 기본값
    (실패를 빨리 드러내기 위한 의도적 설계)은 그대로 둔 채로."""
    client = get_upstage_client()
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format
    if timeout is not None:
        kwargs["timeout"] = timeout
    # reasoning_effort는 Upstage(Solar) API가 지원하는 필드지만, 이 프로젝트에 고정된
    # openai SDK 버전(1.54.0)의 create()는 아직 이 파라미터를 정식 인자로 알지 못해
    # 그대로 넘기면 TypeError로 죽는다(실제 Solar 채팅 호출에서 재현, 2026-07-11).
    # SDK 버전과 무관하게 항상 존재하는 extra_body로 우회해 원시 요청 바디에 실어 보낸다.
    if reasoning_effort is not None:
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
    return await client.chat.completions.create(**kwargs)


async def structured_completion(
    messages: list[dict[str, Any]],
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    model: str = DEFAULT_MODEL,
    reasoning_effort: str | None = "medium",
) -> dict[str, Any]:
    """Structured Outputs(JSON 스키마 강제) 호출 후 파싱된 dict를 반환한다.

    schema는 기획안 3절 요구대로 "단일 라벨 딕셔너리"가 아닌 호출부에서 array-of-events
    형태로 구성해 전달하는 것을 전제로 한다(예: agents/prompts.py의 EVENT_EXTRACTION_SCHEMA).
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
    }
    response = await chat_completion(
        messages,
        model=model,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"Solar structured output '{schema_name}' returned empty content")
    return json.loads(content)
