"""관리자 권한: users.role 컬럼 + 감사 로그 테이블

users.role(user/admin)만으로 관리자 대시보드 접근을 게이트한다 — 별도 인증 체계
없이 기존 Supabase Auth 로그인을 그대로 쓴다. admin_audit_logs는 관리자가 사용자의
개인 서사 데이터(세션 대화·산문)를 조회할 때마다 남기는 최소 감사 기록이다.

Revision ID: 008
Revises: 007
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE userrole AS ENUM ('user', 'admin')")
    t_userrole = PG_ENUM("user", "admin", name="userrole", create_type=False)

    op.add_column(
        "users",
        sa.Column(
            "role", t_userrole, nullable=False, server_default="user",
            comment="관리자 대시보드 접근 게이트. 기본값 user.",
        ),
    )

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "admin_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("target_session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_audit_logs_admin_id", "admin_audit_logs", ["admin_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_logs_admin_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
    op.drop_column("users", "role")
    op.execute("DROP TYPE userrole")
