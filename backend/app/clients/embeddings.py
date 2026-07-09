"""
Upstage Embeddings API 클라이언트 (embedding-query / embedding-passage).

기획안 3절: 저장 시 passage, 검색 시 query로 모델을 분기해 RAG 유사도 매칭 성능을
극대화한다. 벡터는 정규화되어 있어 코사인 유사도 = 내적이다(Upstage 공식 문서).

배치 제약(upstage_embeddings_api_docs.txt): 요청당 최대 100개 텍스트, 배치 합산 최대
204,800 토큰, 텍스트 1개당 최대 4,000 토큰. 이 모듈은 호출부가 이미 이 제약 안에서
청크를 구성했다고 가정하고 별도로 재분할하지 않는다 — 이벤트 단위 문단은 512 토큰
권장선을 넘길 일이 거의 없어 상위 서비스 레이어에서 자연히 충족된다.
"""

from __future__ import annotations

from app.clients.base import get_upstage_client

QUERY_MODEL = "embedding-query"
PASSAGE_MODEL = "embedding-passage"


async def embed_query(text: str) -> list[float]:
    client = get_upstage_client()
    response = await client.embeddings.create(model=QUERY_MODEL, input=text)
    return response.data[0].embedding


async def embed_passages(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = get_upstage_client()
    response = await client.embeddings.create(model=PASSAGE_MODEL, input=texts)
    return [item.embedding for item in response.data]
