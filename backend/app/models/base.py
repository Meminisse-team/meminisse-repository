from enum import Enum as PyEnum
from typing import TypeVar

from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase

# Upstage Embeddings API (embedding-query / embedding-passage).
# 실제 UPSTAGE_API_KEY로 1회 호출해 응답 벡터 길이를 확인한 결과 4096차원으로 검증됨
# (upstage_embeddings_api_docs.txt의 1024차원 서술은 오기로 판단). pgvector HNSW/IVFFlat
# 인덱스는 2000~4000차원까지만 지원해 이 차원에는 근사 인덱스를 만들 수 없으므로,
# events.embedding은 인덱스 없이 순차 스캔으로 검색한다(alembic/versions/002 참조).
EMBEDDING_DIM = 4096


class Base(DeclarativeBase):
    pass


_E = TypeVar("_E", bound=PyEnum)


def str_enum(enum_cls: type[_E], *, name: str) -> SAEnum:
    """
    이 프로젝트의 모든 enum은 `class X(str, PyEnum): MEMBER = "member_value"` 형태이고,
    alembic 마이그레이션이 Postgres enum 타입을 소문자 value(예: 'onboarding')로 생성한다.

    반면 sa.Enum(enum_cls, ...)의 기본 동작은 values_callable을 지정하지 않으면 파이썬
    쪽 멤버 '이름'(예: 'ONBOARDING')을 그대로 DB에 바인딩한다 — 실제 INSERT 시 Postgres
    enum 라벨과 불일치해 InvalidTextRepresentationError로 즉시 실패한다. 인메모리 Mock
    게이트웨이는 실제 enum 타입 제약이 없어 이 버그를 절대 드러내지 못하므로, 실제
    Supabase 연동 스모크 테스트에서만 재현되었다. 모든 모델의 Enum 컬럼은 raw
    `sqlalchemy.Enum(...)` 대신 반드시 이 헬퍼를 통해 선언할 것.
    """
    return SAEnum(enum_cls, name=name, values_callable=lambda obj: [member.value for member in obj])
