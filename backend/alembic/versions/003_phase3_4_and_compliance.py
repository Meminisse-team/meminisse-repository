"""Phase 3/4 scaffolding + 제3자 보호 + 동의 기록

기획안의 Phase 3(이벤트 병합·중요도 산정·스타일 바이블), Phase 4(동적 목차·하향식 집필·
팩트체크·근거검증·제3자 위해성 분류)와 5/6절(동의 주체 분리, 등장인물 검토·가명화 기본값,
주의의무 이행 증빙)을 스키마에 반영한다.

- autobiographies: style_bible(Phase 3 산출물), book_synopsis(Phase 4 1단계 산출물),
  raw_log_retention_until(원문 로그 최소보유 원칙 이행) 추가.
- chapter_drafts: chapter_synopsis(Phase 4 2단계 산출물), factcheck_report/
  groundedness_report(최종 검토 화면 '출처 보기'가 참조하는 정량 검증 결과) 추가.
- events: importance_score/importance_signals(재현 가능한 목차 반영 근거),
  life_milestone_category(회상요법 문헌 기반 범주 매칭 신호) 추가.
- characters/character_mentions: 구술자 외 제3자 인물 레코드. real_name_retained는
  전수 가명화 opt-out 정책에 따라 기본값 false.
- consent_records: 정보주체 동의(수집·이용/실명유지/보관연장) 기록. 고지 문구 버전과
  동의 시각을 보존하여 분쟁 시 고지 의무 이행을 입증.

Revision ID: 003
Revises: 002
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Enum types                                                           #
    # ------------------------------------------------------------------ #
    op.execute(
        "CREATE TYPE lifemilestonecategory AS ENUM "
        "('marriage', 'childbirth', 'career_change', 'illness', 'bereavement', "
        "'relocation', 'retirement', 'other')"
    )
    op.execute(
        "CREATE TYPE riskclassification AS ENUM "
        "('none', 'negative_portrayal', 'conflict', 'crime_mention')"
    )
    op.execute(
        "CREATE TYPE consenttype AS ENUM "
        "('data_collection', 'disclosure_realname', 'retention_extension')"
    )
    op.execute("CREATE TYPE consentgrantedby AS ENUM ('self', 'guardian')")

    t_lifemilestonecategory = PG_ENUM(
        "marriage", "childbirth", "career_change", "illness", "bereavement",
        "relocation", "retirement", "other",
        name="lifemilestonecategory", create_type=False,
    )
    t_riskclassification = PG_ENUM(
        "none", "negative_portrayal", "conflict", "crime_mention",
        name="riskclassification", create_type=False,
    )
    t_consenttype = PG_ENUM(
        "data_collection", "disclosure_realname", "retention_extension",
        name="consenttype", create_type=False,
    )
    t_consentgrantedby = PG_ENUM(
        "self", "guardian", name="consentgrantedby", create_type=False,
    )

    # ------------------------------------------------------------------ #
    # autobiographies                                                      #
    # ------------------------------------------------------------------ #
    op.add_column(
        "autobiographies",
        sa.Column(
            "style_bible", JSONB, nullable=True,
            comment="Phase 3: 화자 문체·상용 표현·가치관 키워드·감정 아크. 전 집필 프롬프트에 전역 주입.",
        ),
    )
    op.add_column(
        "autobiographies",
        sa.Column("book_synopsis", sa.Text, nullable=True, comment="Phase 4 1단계: 책 전체 시놉시스"),
    )
    op.add_column(
        "autobiographies",
        sa.Column(
            "raw_log_retention_until", sa.Date, nullable=True,
            comment="최종 확정 후 원문 로그 자동 삭제 기준일. 옵트인 시에만 연장.",
        ),
    )

    # ------------------------------------------------------------------ #
    # chapter_drafts                                                       #
    # ------------------------------------------------------------------ #
    op.add_column(
        "chapter_drafts",
        sa.Column("chapter_synopsis", sa.Text, nullable=True, comment="Phase 4 2단계: 챕터 시놉시스"),
    )
    op.add_column(
        "chapter_drafts",
        sa.Column(
            "factcheck_report", JSONB, nullable=True,
            comment="원문 대조 팩트체크(재추출-정규화-대조) 결과. 라벨 불일치 플래그 목록.",
        ),
    )
    op.add_column(
        "chapter_drafts",
        sa.Column(
            "groundedness_report", JSONB, nullable=True,
            comment="근거 검증(Groundedness Check) 결과. 무근거 문장 NLI 판정 플래그 목록.",
        ),
    )

    # ------------------------------------------------------------------ #
    # events                                                               #
    # ------------------------------------------------------------------ #
    op.add_column(
        "events",
        sa.Column(
            "importance_score", sa.Numeric(9, 3), nullable=True,
            comment="Phase 3 중요도 스코어링 결과값(목차 후보 정렬 키). precision=9: "
            "'꼭 넣기' 고정 가산점(1000)이 다른 신호를 압도해야 하므로 여유를 둔다.",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "importance_signals", JSONB, nullable=True,
            comment="중요도 산정 근거 스냅샷(분량/반복횟수/이정표매칭/z-score 등).",
        ),
    )
    op.add_column(
        "events",
        sa.Column("life_milestone_category", t_lifemilestonecategory, nullable=True),
    )

    # ------------------------------------------------------------------ #
    # characters                                                           #
    # ------------------------------------------------------------------ #
    op.create_table(
        "characters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "autobiography_id", UUID(as_uuid=True),
            sa.ForeignKey("autobiographies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("display_name", sa.String(100), nullable=False, comment="원고에 노출되는 이름(기본값: 가명)"),
        sa.Column("real_name", sa.String(100), nullable=True),
        sa.Column("relation_to_user", sa.String(100), nullable=True),
        sa.Column(
            "risk_classification", t_riskclassification, nullable=False, server_default="none",
        ),
        sa.Column(
            "real_name_retained", sa.Boolean, nullable=False, server_default=sa.text("false"),
            comment="전수 가명화 opt-out. true 전환은 인물 단위 법적 책임 고지 동의 후에만 허용.",
        ),
        sa.Column("disclosure_notice_version", sa.String(50), nullable=True),
        sa.Column("disclosure_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_characters_autobiography_id", "characters", ["autobiography_id"])

    # ------------------------------------------------------------------ #
    # character_mentions                                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "character_mentions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "character_id", UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "event_id", UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "chapter_draft_id", UUID(as_uuid=True),
            sa.ForeignKey("chapter_drafts.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_character_mentions_character_id", "character_mentions", ["character_id"])
    op.create_index("ix_character_mentions_event_id", "character_mentions", ["event_id"])
    op.create_index("ix_character_mentions_chapter_draft_id", "character_mentions", ["chapter_draft_id"])

    # ------------------------------------------------------------------ #
    # consent_records                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "consent_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("consent_type", t_consenttype, nullable=False),
        sa.Column("notice_version", sa.String(50), nullable=False),
        sa.Column("granted_by", t_consentgrantedby, nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_consent_records_user_id", "consent_records", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_consent_records_user_id", table_name="consent_records")
    op.drop_table("consent_records")

    op.drop_index("ix_character_mentions_chapter_draft_id", table_name="character_mentions")
    op.drop_index("ix_character_mentions_event_id", table_name="character_mentions")
    op.drop_index("ix_character_mentions_character_id", table_name="character_mentions")
    op.drop_table("character_mentions")

    op.drop_index("ix_characters_autobiography_id", table_name="characters")
    op.drop_table("characters")

    op.drop_column("events", "life_milestone_category")
    op.drop_column("events", "importance_signals")
    op.drop_column("events", "importance_score")

    op.drop_column("chapter_drafts", "groundedness_report")
    op.drop_column("chapter_drafts", "factcheck_report")
    op.drop_column("chapter_drafts", "chapter_synopsis")

    op.drop_column("autobiographies", "raw_log_retention_until")
    op.drop_column("autobiographies", "book_synopsis")
    op.drop_column("autobiographies", "style_bible")

    op.execute("DROP TYPE IF EXISTS consentgrantedby")
    op.execute("DROP TYPE IF EXISTS consenttype")
    op.execute("DROP TYPE IF EXISTS riskclassification")
    op.execute("DROP TYPE IF EXISTS lifemilestonecategory")
