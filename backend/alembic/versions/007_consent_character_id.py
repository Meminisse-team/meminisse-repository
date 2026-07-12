"""등장인물 실명 동의를 인물 단위로 세분화: consent_records.character_id 추가

P4 컴플라이언스 마감: character_service.retain_real_name이 지금까지 "이 사용자가
실명 유지 고지에 최소 1회 동의했는가"라는 완화된(사용자 단위) 게이트로 동작했다 —
기획안이 요구하는 "인물 단위" 동의가 아니었다(같은 자서전에 등장하는 인물 A에 대한
동의로 인물 B의 실명까지 유지될 수 있는 허점). character_id를 nullable로 추가해
DISCLOSURE_REALNAME 동의는 반드시 특정 인물에 연결되도록 하고(DATA_COLLECTION 등
기존 사용자 단위 동의는 그대로 null), 게이트를 인물 단위로 좁힌다.

Revision ID: 007
Revises: 006
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "consent_records",
        sa.Column(
            "character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=True,
            comment="DISCLOSURE_REALNAME 동의를 특정 인물에 묶는다. 사용자 단위 동의"
            "(DATA_COLLECTION 등)는 null.",
        ),
    )
    op.create_index(
        "ix_consent_records_character_id", "consent_records", ["character_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_consent_records_character_id", table_name="consent_records")
    op.drop_column("consent_records", "character_id")
