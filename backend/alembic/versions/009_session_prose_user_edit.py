"""'나의 이야기' 산문 사용자 편집: 원본 백업 + 편집 시각

사용자가 재조립된 session_prose가 마음에 들지 않을 때 직접 고쳐 저장할 수 있게
한다(story_service.update_session_prose). session_prose_original은 AI가 최초
재조립한 원본을 최초 편집 시점에만 백업해두는 컬럼이고(이후 재편집으로 덮어쓰지
않음), prose_last_edited_at은 재추출(이벤트 추출 LLM 재호출) 연타를 막는 쿨다운
판정에 쓰인다.

Revision ID: 009
Revises: 008
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column(
            "session_prose_original", sa.Text, nullable=True,
            comment="AI가 최초 재조립한 산문 원본. 사용자가 처음 편집할 때만 채워진다.",
        ),
    )
    op.add_column(
        "interview_sessions",
        sa.Column(
            "prose_last_edited_at", sa.DateTime(timezone=True), nullable=True,
            comment="마지막 사용자 편집 저장 시각. 재추출 쿨다운 판정에 사용.",
        ),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "prose_last_edited_at")
    op.drop_column("interview_sessions", "session_prose_original")
