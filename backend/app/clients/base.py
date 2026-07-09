"""
Upstage Chat Completions / Embeddings는 OpenAI SDK와 호환되므로(upstage_solar_api_docs.txt,
upstage_embeddings_api_docs.txt의 "OpenAI SDK Compatible" 섹션 참조) base_url만 갈아끼운
AsyncOpenAI 클라이언트 하나를 Solar와 Embeddings가 함께 재사용한다. Document Parse는
multipart/form-data 업로드가 필요해 OpenAI SDK로 감쌀 수 없으므로 clients/document_parse.py
에서 httpx로 별도 처리한다.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from app.config import settings

UPSTAGE_BASE_URL = "https://api.upstage.ai/v1"


@lru_cache(maxsize=1)
def get_upstage_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.UPSTAGE_API_KEY, base_url=UPSTAGE_BASE_URL)
