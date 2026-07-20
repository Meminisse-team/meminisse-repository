"""
자서전 집필(Phase 3/4) LLM 호출을 settings.AUTOBIOGRAPHY_LLM_PROVIDER에 따라 Solar,
Claude 중 하나로 라우팅한다. app/services/autobiography_service.py와
app/services/character_service.py만 이 모듈을 통해 LLM을 호출한다 — 이 파이프라인 밖의
호출부(event_extraction_service, interview_service 등)는 이 설정과 무관하게 항상
app.clients.solar를 직접 쓴다.

기본값("solar")은 .env에서 바뀌지 않는다 — 계정 단위 실험 전환은 스크립트가 런타임에
settings.AUTOBIOGRAPHY_LLM_PROVIDER를 잠깐 덮어쓰는 방식으로 처리한다(backend/scripts 참조).
"""

from __future__ import annotations

from typing import Any

from app.clients import claude, solar
from app.config import settings

_PROVIDERS = {"claude": claude}


async def chat_completion(messages: list[dict[str, Any]], **kwargs: Any):
    provider = _PROVIDERS.get(settings.AUTOBIOGRAPHY_LLM_PROVIDER, solar)
    return await provider.chat_completion(messages, **kwargs)


async def structured_completion(messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    provider = _PROVIDERS.get(settings.AUTOBIOGRAPHY_LLM_PROVIDER, solar)
    return await provider.structured_completion(messages, **kwargs)
