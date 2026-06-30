"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536  # OpenAI text-embedding-3-large


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Extensions                                                           #
    # ------------------------------------------------------------------ #
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------ #
    # Enum types                                                           #
    # ------------------------------------------------------------------ #
    op.execute("CREATE TYPE userstage AS ENUM ('onboarding', 'interview', 'publishing', 'published')")
    op.execute("CREATE TYPE lifeperiod AS ENUM ('childhood', 'youth', 'adulthood', 'senior')")
    op.execute("CREATE TYPE mediaanalysistrack AS ENUM ('text_document', 'pure_memory')")
    op.execute("CREATE TYPE sessiontype AS ENUM ('photo', 'fixed_question')")
    op.execute("CREATE TYPE sessionstatus AS ENUM ('open', 'completed', 'skipped')")
    op.execute("CREATE TYPE messagerole AS ENUM ('user', 'assistant', 'system')")
    op.execute("CREATE TYPE assettype AS ENUM ('image', 'audio', 'video', 'document')")
    op.execute("CREATE TYPE draftstatus AS ENUM ('draft', 'reviewed', 'finalized')")
    op.execute("CREATE TYPE autobiographystatus AS ENUM ('in_progress', 'consolidated', 'published')")

    # ------------------------------------------------------------------ #
    # questions (users.current_question_id FK 선행 필요)                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sequence_order", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "life_period",
            sa.Enum(name="lifeperiod", create_type=False),
            nullable=False,
            server_default="childhood",
            comment="타임라인 정렬용 메타데이터. 챕터 구분 기준 아님.",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("sequence_order", name="uq_questions_sequence_order"),
    )

    # ------------------------------------------------------------------ #
    # users                                                                #
    # ------------------------------------------------------------------ #
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("birth_year", sa.SmallInteger, nullable=True),      # 당시 나이 기반 생애주기 매핑
        sa.Column("hometown", sa.String(255), nullable=True),          # 고향 (초기 프로필)
        sa.Column(
            "current_stage",
            sa.Enum(name="userstage", create_type=False),
            nullable=False,
            server_default="onboarding",
        ),
        sa.Column(
            "current_question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ------------------------------------------------------------------ #
    # media_assets                                                         #
    # ------------------------------------------------------------------ #
    # session_id FK는 interview_sessions 생성 후 ALTER로 추가 (순환 참조 해소).
    op.create_table(
        "media_assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),  # FK constraint 후행 추가
        sa.Column("s3_key", sa.String(1024), nullable=False),
        sa.Column("s3_url", sa.String(2048), nullable=False),
        sa.Column(
            "asset_type",
            sa.Enum(name="assettype", create_type=False),
            nullable=False,
            server_default="image",
        ),
        # Phase 1 업로드 메타데이터 (유저 선택 입력)
        sa.Column("age_at_time", sa.SmallInteger, nullable=True, comment="당시 나이 → 생애주기 큐 매핑"),
        sa.Column("location_at_time", sa.String(255), nullable=True, comment="당시 장소"),
        sa.Column("people_at_time", sa.Text, nullable=True, comment="당시 인물"),
        # Phase 1 듀얼 트랙 처리 결과
        sa.Column(
            "life_period_mapped",
            sa.Enum(name="lifeperiod", create_type=False),
            nullable=True,
            comment="age_at_time 기반 매핑 결과. 생애주기 인터뷰 큐 우선순위 분류.",
        ),
        sa.Column(
            "analysis_track",
            sa.Enum(name="mediaanalysistrack", create_type=False),
            nullable=True,
            comment="text_document=Upstage Parse 경로, pure_memory=유저 코멘트 경로",
        ),
        sa.Column("pre_extracted_labels", JSONB, nullable=True, comment="Upstage Document Parse API 추출 라벨"),
        sa.Column("user_comment", sa.Text, nullable=True, comment="순수 추억 사진의 1차 유저 코멘트"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("s3_key", name="uq_media_assets_s3_key"),
    )
    op.create_index("ix_media_assets_user_id", "media_assets", ["user_id"])

    # ------------------------------------------------------------------ #
    # interview_sessions                                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "interview_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "session_type",
            sa.Enum(name="sessiontype", create_type=False),
            nullable=False,
            server_default="fixed_question",
            comment="photo=사진 핀셋 대화, fixed_question=고정 템플릿 질문",
        ),
        # FIXED_QUESTION 세션 전용 (nullable: PHOTO 세션이면 null)
        sa.Column("question_id", UUID(as_uuid=True), sa.ForeignKey("questions.id"), nullable=True),
        # PHOTO 세션 전용: 이 대화가 기반하는 원본 사진
        sa.Column(
            "linked_media_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("media_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(name="sessionstatus", create_type=False),
            nullable=False,
            server_default="open",
        ),
        sa.Column(
            "slots_filled",
            JSONB,
            nullable=False,
            server_default="{}",
            comment="11개 슬롯(필수5+추가6) 충족 현황. 꼬리질문 루프 판단 기준.",
        ),
        sa.Column(
            "followup_count",
            sa.SmallInteger,
            nullable=False,
            server_default="0",
            comment="꼬리질문 발동 횟수. 2회 이상이면 필수 라벨 미충족이어도 다음 단계로 진행.",
        ),
        sa.Column(
            "is_must_include",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="유저의 꼭 넣기 체크 여부. 목차 생성 시 우선 반영.",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_interview_sessions_user_id", "interview_sessions", ["user_id"])

    # ------------------------------------------------------------------ #
    # media_assets.session_id FK 후행 추가 (순환 참조 해소)                #
    # ------------------------------------------------------------------ #
    op.create_foreign_key(
        "fk_media_assets_session_id",
        "media_assets",
        "interview_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------ #
    # chat_logs                                                            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "chat_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Enum(name="messagerole", create_type=False), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("extracted_labels", JSONB, nullable=True, comment="user 턴 전용: 11개 슬롯 추출값"),
        sa.Column("embedding", sa.Text, nullable=True),  # 아래에서 vector 타입으로 교체
        sa.Column("turn_index", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_id", "turn_index", name="uq_chat_logs_session_turn"),
    )
    # embedding 컬럼을 pgvector 네이티브 타입(1536차원)으로 교체
    op.execute(
        f"ALTER TABLE chat_logs ALTER COLUMN embedding TYPE vector({EMBEDDING_DIM}) "
        f"USING NULL::vector({EMBEDDING_DIM})"
    )
    op.create_index("ix_chat_logs_session_id", "chat_logs", ["session_id"])
    # HNSW 인덱스: 코사인 유사도 기반 ANN 검색 (챕터 조립 하이브리드 검색 핵심)
    op.execute(
        f"CREATE INDEX ix_chat_logs_embedding_hnsw ON chat_logs "
        f"USING hnsw (embedding vector_cosine_ops) "
        f"WITH (m = 16, ef_construction = 64) "
        f"WHERE embedding IS NOT NULL"
    )

    # ------------------------------------------------------------------ #
    # autobiographies                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "autobiographies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column(
            "status",
            sa.Enum(name="autobiographystatus", create_type=False),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column(
            "consolidated_content",
            sa.Text,
            nullable=True,
            comment="Phase 3: 대화별 Raw 로그 → 1인칭 산문 재조립 결과. 누락 금지. status=consolidated 진입 조건.",
        ),
        sa.Column(
            "toc_data",
            JSONB,
            nullable=True,
            comment='Phase 4: LLM 목차 후보 3개 + 유저 선택. {"candidates":[{"index":0,"chapters":[...]}],"selected_candidate_index":null}',
        ),
        sa.Column("final_content", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_autobiographies_user_id"),
    )

    # ------------------------------------------------------------------ #
    # chapter_drafts                                                       #
    # ------------------------------------------------------------------ #
    op.create_table(
        "chapter_drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "autobiography_id",
            UUID(as_uuid=True),
            sa.ForeignKey("autobiographies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_index", sa.Integer, nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column(
            "source_session_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
            comment="이 챕터 생성에 기여한 세션 ID 목록 (M:N, 단일 FK 불가)",
        ),
        sa.Column(
            "status",
            sa.Enum(name="draftstatus", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("autobiography_id", "chapter_index", name="uq_chapter_drafts_auto_idx"),
    )
    op.create_index("ix_chapter_drafts_autobiography_id", "chapter_drafts", ["autobiography_id"])


def downgrade() -> None:
    op.drop_table("chapter_drafts")
    op.drop_table("autobiographies")
    op.execute("DROP INDEX IF EXISTS ix_chat_logs_embedding_hnsw")
    op.drop_table("chat_logs")
    # 순환 FK 제약 먼저 제거 후 테이블 드롭
    op.drop_constraint("fk_media_assets_session_id", "media_assets", type_="foreignkey")
    op.drop_table("interview_sessions")
    op.drop_table("media_assets")
    op.drop_table("users")
    op.drop_table("questions")

    op.execute("DROP TYPE IF EXISTS draftstatus")
    op.execute("DROP TYPE IF EXISTS autobiographystatus")
    op.execute("DROP TYPE IF EXISTS assettype")
    op.execute("DROP TYPE IF EXISTS messagerole")
    op.execute("DROP TYPE IF EXISTS sessionstatus")
    op.execute("DROP TYPE IF EXISTS sessiontype")
    op.execute("DROP TYPE IF EXISTS mediaanalysistrack")
    op.execute("DROP TYPE IF EXISTS lifeperiod")
    op.execute("DROP TYPE IF EXISTS userstage")

    op.execute("DROP EXTENSION IF EXISTS vector")
