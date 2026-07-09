"""Event as first-class object + Upstage embedding migration

기획안 원칙 1(이벤트 1급 객체화)을 스키마에 반영한다.

- chat_logs: 턴 단위로 붙어있던 extracted_labels/embedding 제거. 슬롯 게이팅 결과는
  대화 중 저비용 판별로 즉시 소비·폐기되는 값이라 원래 영속화 대상이 아니었다.
- interview_sessions: session_prose 추가 (Layer 2, 세션 종료 후 재조립되는 1인칭 산문).
- events / event_relations: 사건 단위 레코드(라벨+요약+산문 문단+임베딩+출처+verified 게이트)와
  사건 간 관계를 위한 신규 테이블.
- chapter_drafts.source_session_ids → source_event_ids: 챕터 조립의 실제 검색 단위가
  세션이 아닌 이벤트이므로 참조 대상을 이벤트로 교정.
- 임베딩 차원을 OpenAI(1536) → Upstage embedding-query/embedding-passage로 전환.
  실제 UPSTAGE_API_KEY로 embedding-query/embedding-passage를 1회 호출해 확인한 결과
  4096차원으로 검증됨(upstage_embeddings_api_docs.txt의 1024차원 서술은 오기로 판단).
  단, pgvector의 HNSW/IVFFlat 인덱스는 vector 타입 기준 2000차원, halfvec 타입도
  4000차원까지만 지원하므로(Supabase pgvector 0.8.2 실측) 4096차원에는 근사 인덱스를
  아예 만들 수 없다 — 저장 자체는 문제없다(vector 타입 저장 한계는 16,000차원). 따라서
  events.embedding에는 인덱스 없이 컬럼만 두고 순차 스캔으로 검색한다(하단 참조).

  향후 개발 과제(지금은 의도적으로 보류): 유저당 이벤트 수가 늘어 순차 스캔이 실제로
  느려지면, (1) PCA/랜덤 프로젝션으로 4096차원을 2000차원 이하로 축소한 벡터를 별도
  컬럼에 추가 저장하고 그 축소 벡터에만 HNSW를 걸어 "인덱스로 후보 N개 추출 → 원본
  4096차원으로 재정렬(rerank)"하는 2단계 검색을 도입하거나, (2) pgvector의 이진
  양자화(bit 타입 + 해밍 거리)로 더 가벼운 1차 필터를 두는 방법이 있다. 둘 다 기존
  순차 스캔 쿼리를 건드리지 않는 순수 추가(additive) 마이그레이션으로 붙일 수 있다.
  단순히 앞쪽 N차원만 잘라 쓰는 truncation은 Upstage 임베딩이 Matryoshka 방식으로
  학습되었다는 근거가 없어 검증 없이는 채택하지 않는다.

Revision ID: 002
Revises: 001
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 4096  # Upstage embedding-query / embedding-passage — 실제 API 응답으로 검증됨


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # chat_logs: 턴=이벤트 1:1 구조 폐기                                    #
    # ------------------------------------------------------------------ #
    op.execute("DROP INDEX IF EXISTS ix_chat_logs_embedding_hnsw")
    op.drop_column("chat_logs", "embedding")
    op.drop_column("chat_logs", "extracted_labels")

    # ------------------------------------------------------------------ #
    # interview_sessions: Layer 2 세션 산문                                #
    # ------------------------------------------------------------------ #
    op.add_column(
        "interview_sessions",
        sa.Column(
            "session_prose",
            sa.Text,
            nullable=True,
            comment="세션 종료 후 재조립된 1인칭 산문(Layer 2). Event.prose_paragraph의 원본.",
        ),
    )

    # ------------------------------------------------------------------ #
    # Enum types                                                           #
    # ------------------------------------------------------------------ #
    op.execute("CREATE TYPE eventsourcetype AS ENUM ('session_chat', 'document')")
    op.execute("CREATE TYPE eventrelationtype AS ENUM ('cause', 'overcome', 'followed_by', 'related')")

    t_eventsourcetype = PG_ENUM(
        "session_chat", "document", name="eventsourcetype", create_type=False,
    )
    t_eventrelationtype = PG_ENUM(
        "cause", "overcome", "followed_by", "related", name="eventrelationtype", create_type=False,
    )
    t_lifeperiod = PG_ENUM(
        "childhood", "youth", "adulthood", "senior", name="lifeperiod", create_type=False,
    )

    # ------------------------------------------------------------------ #
    # events                                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", t_eventsourcetype, nullable=False),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "media_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("media_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_span", JSONB, nullable=True, comment="원문 대조 재생성용 근거 포인터"),
        sa.Column("life_period", t_lifeperiod, nullable=True),
        sa.Column("occurred_at_label", sa.String(100), nullable=True, comment="상대적/범위형 시기 표현"),
        sa.Column("place", sa.String(255), nullable=True),
        sa.Column("people", sa.Text, nullable=True),
        sa.Column("one_line_summary", sa.Text, nullable=False),
        sa.Column("prose_paragraph", sa.Text, nullable=False, comment="대응 산문 문단. 그 자체가 RAG 검색 소스."),
        sa.Column("emotion_tag", sa.String(50), nullable=True),
        sa.Column("emotion_intensity", sa.SmallInteger, nullable=True),
        sa.Column("emotion_inferred", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("labels", JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", JSONB, nullable=True),
        sa.Column(
            "verified",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="Layer 1 검증 게이트. false면 embedding null 유지, RAG/집필 제외.",
        ),
        sa.Column("is_must_include", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "duplicate_of_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_events_user_id", "events", ["user_id"])
    op.create_index("ix_events_session_id", "events", ["session_id"])
    op.create_index("ix_events_media_asset_id", "events", ["media_asset_id"])
    # HNSW(및 IVFFlat) 인덱스는 pgvector에서 vector 타입 2000차원, halfvec 타입도
    # 4000차원까지만 지원한다(실측: Supabase pgvector 0.8.2). 실제 Upstage
    # embedding-query/embedding-passage 응답은 4096차원으로 확인되어(EMBEDDING_DIM,
    # app/models/base.py) 두 인덱스 타입 모두 애초에 생성이 불가능하다. 따라서 근사
    # 인덱스 없이 vector(4096) 컬럼만 두고, 유사도 검색은 순차 스캔
    # (ORDER BY embedding <=> query LIMIT N)으로 수행한다 — 해커톤 규모(유저당
    # 수십~수백 이벤트)에서는 성능상 문제가 되지 않는다. 규모가 커졌을 때의
    # 대안(차원 축소+rerank, 이진 양자화)은 이 파일 상단 모듈 docstring 참조.

    # ------------------------------------------------------------------ #
    # event_relations                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "event_relations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("from_event_id", UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_event_id", UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", t_eventrelationtype, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_event_relations_from_event_id", "event_relations", ["from_event_id"])
    op.create_index("ix_event_relations_to_event_id", "event_relations", ["to_event_id"])

    # ------------------------------------------------------------------ #
    # chapter_drafts: 세션 참조 → 이벤트 참조                              #
    # ------------------------------------------------------------------ #
    op.alter_column(
        "chapter_drafts",
        "source_session_ids",
        new_column_name="source_event_ids",
        existing_comment="이 챕터 생성에 기여한 세션 ID 목록",
    )


def downgrade() -> None:
    op.alter_column(
        "chapter_drafts",
        "source_event_ids",
        new_column_name="source_session_ids",
    )

    op.drop_index("ix_event_relations_to_event_id", table_name="event_relations")
    op.drop_index("ix_event_relations_from_event_id", table_name="event_relations")
    op.drop_table("event_relations")

    op.drop_index("ix_events_media_asset_id", table_name="events")
    op.drop_index("ix_events_session_id", table_name="events")
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_table("events")

    op.execute("DROP TYPE IF EXISTS eventrelationtype")
    op.execute("DROP TYPE IF EXISTS eventsourcetype")

    op.drop_column("interview_sessions", "session_prose")

    op.add_column(
        "chat_logs",
        sa.Column("extracted_labels", JSONB, nullable=True, comment="user 턴 전용: 11개 슬롯 추출값"),
    )
    op.add_column("chat_logs", sa.Column("embedding", sa.Text, nullable=True))
    op.execute(
        f"ALTER TABLE chat_logs ALTER COLUMN embedding TYPE vector(1536) "
        f"USING NULL::vector(1536)"
    )
    op.execute(
        "CREATE INDEX ix_chat_logs_embedding_hnsw ON chat_logs "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64) "
        "WHERE embedding IS NOT NULL"
    )
