from sqlalchemy.orm import DeclarativeBase

# Upstage Embeddings API (embedding-query / embedding-passage).
# upstage_embeddings_api_docs.txt 내에서 차원 수가 서술부(4096)와 공식 스펙부(1024)로
# 서로 모순되어 있어, 실제 UPSTAGE_API_KEY로 1회 호출해 응답 벡터 길이를 확인하기 전까지는
# 잠정값이다. 값이 다르면 이 상수와 alembic 마이그레이션의 vector(N) 캐스트를 함께 수정할 것.
EMBEDDING_DIM = 4096


class Base(DeclarativeBase):
    pass
