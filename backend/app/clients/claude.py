"""
Claude(Anthropic) 채팅 완성 클라이언트 — 자서전 집필(Phase 3/4) 세 번째 실험 프로바이더.

app/clients/solar.py·gemini.py와 같은 시그니처(messages 배열을 넣으면 응답/파싱된 dict를
받는 얇은 래퍼)를 유지해 app/clients/llm_router.py가 세 프로바이더를 투명하게 스위칭할 수
있게 한다. anthropic==0.117.0(설치본) 기준 실제 SDK 시그니처를 introspect해서 작성했다.

Solar의 reasoning_effort(low/medium/high)는 Claude의 output_config.effort와 값 자체가
그대로 호환된다(별도 매핑 없이 통과) — Gemini의 thinking_budget 토큰 수 근사 매핑보다
훨씬 깔끔하다. structured_completion의 json_schema도 변환 없이 그대로 재사용한다 —
Claude Structured Outputs는 additionalProperties=false를 포함한 OpenAI 스타일 strict
JSON Schema를 그대로 지원한다(claude-api 스킬 문서 확인).

max_tokens는 Solar/Gemini와 달리 Anthropic Messages API에서 필수 파라미터라, 호출부가
생략하면 이 모듈이 기본값을 채운다. 항상 streaming(client.messages.stream +
get_final_message)으로 호출한다 — 논스트리밍 경로는 큰 max_tokens에서 SDK가 자체
타임아웃 추정으로 ValueError를 던지는 가드가 있다(claude-api 스킬의 권장 사항).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import anthropic

from app.config import settings

DEFAULT_MODEL = settings.CLAUDE_MODEL

# 호출부가 max_tokens를 생략하는 경우(스타일 바이블·시놉시스류·구조화 출력 등)를 위한
# 기본값 — Anthropic API는 이 필드가 필수라 Solar처럼 "생략하면 서버 기본값" 동작이
# 불가능하다. max_tokens는 adaptive thinking 토큰까지 포함한 총량이라, "본문 자체는
# 짧다"고 8192처럼 낮게 잡으면 위험하다 — 실측(2026-07-20, billgates 계정 목차
# 재생성) 결과 toc_generation 구조화 출력(3개 후보 × 최대 19개 챕터) 호출이
# thinking에 예산을 먼저 뺏겨 max_tokens=8192에서 JSON이 중간에 잘리고
# JSONDecodeError로 죽는 사고가 실제로 재현됐다. 챕터 집필(24000)·최종 윤문
# 배치(32000) 등 이 프로젝트에서 가장 큰 출력에 쓰는 상한과 맞춰 여유를 크게 둔다.
_DEFAULT_MAX_TOKENS = 32000


@lru_cache(maxsize=1)
def get_claude_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


@dataclass
class _Message:
    content: str | None


@dataclass
class _Choice:
    message: _Message


@dataclass
class _ChatCompletion:
    """solar.chat_completion이 반환하는 openai ChatCompletion의 호출부 사용 범위
    (`response.choices[0].message.content`)만 흉내 낸 duck-type 응답."""

    choices: list[_Choice]


def _to_claude_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI 스타일 messages를 Claude의 (system, messages) 형태로 변환한다.
    Claude는 system을 별도 최상위 파라미터로 받고, messages 배열에는 user/assistant만
    허용한다(system 역할이 배열 안에 있으면 안 됨)."""
    system_parts: list[str] = []
    claude_messages: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "system":
            system_parts.append(message["content"])
        else:
            claude_messages.append({"role": message["role"], "content": message["content"]})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, claude_messages


def _extract_text(content_blocks: list[Any]) -> str:
    """thinking/text 블록이 섞인 response.content에서 text 블록만 이어붙인다.
    stop_reason == "refusal"인 경우 content가 비어 있을 수 있다 — 빈 문자열을 반환하고
    판단은 호출부(structured_completion의 빈 응답 체크 등)에 맡긴다."""
    return "".join(block.text for block in content_blocks if block.type == "text")


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> _ChatCompletion:
    """timeout: solar.chat_completion과 동일하게 이 호출 하나에만 적용할 초 단위
    타임아웃. temperature는 Claude Sonnet 5에서 비-기본값이면 400이라 그대로
    전달하지 않는다 — 이 프로젝트 호출부 중 temperature를 쓰는 곳이 없어 실질적
    제약은 없다(프롬프트로 문체를 제어하는 기존 설계와 일치)."""
    client = get_claude_client()
    system, claude_messages = _to_claude_messages(messages)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS,
        "messages": claude_messages,
        "thinking": {"type": "adaptive"},
    }
    if system is not None:
        kwargs["system"] = system
    if reasoning_effort is not None:
        kwargs["output_config"] = {"effort": reasoning_effort}
    if response_format is not None:
        json_schema = (response_format.get("json_schema") or {}).get("schema")
        if json_schema is not None:
            output_config = kwargs.get("output_config", {})
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
            kwargs["output_config"] = output_config
    if timeout is not None:
        kwargs["timeout"] = timeout
    if temperature is not None:
        # 의도적으로 무시 — Sonnet 5는 기본값이 아닌 temperature를 400으로 거부하고,
        # 이 프로젝트는 애초에 temperature를 쓰는 호출부가 없다(위 docstring 참조).
        pass

    async with client.messages.stream(**kwargs) as stream:
        message = await stream.get_final_message()
    return _ChatCompletion(choices=[_Choice(message=_Message(content=_extract_text(message.content)))])


async def structured_completion(
    messages: list[dict[str, Any]],
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    model: str = DEFAULT_MODEL,
    reasoning_effort: str | None = "medium",
) -> dict[str, Any]:
    """solar.structured_completion과 동일한 시그니처. schema_name은 Claude
    Structured Outputs에는 별도 자리가 없어(스키마 이름을 요구하지 않음) 여기서는
    에러 메시지 용도로만 쓰인다."""
    response_format = {"json_schema": {"name": schema_name, "schema": json_schema}}
    response = await chat_completion(
        messages, model=model, reasoning_effort=reasoning_effort, response_format=response_format
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError(f"Claude structured output '{schema_name}' returned empty content")
    return json.loads(content)
