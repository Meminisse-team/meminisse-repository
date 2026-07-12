"""OCR 확인질문을 인터뷰 턴에 연결: interview_sessions.pending_ocr_confirmation_event_id 추가

P4 컴플라이언스 마감: Document Parse 경로에서 verified=false로 격리된 이벤트가
지금까지 승격 경로 없이 영구히 고립돼 있었다(app/services/media_service.py
docstring에 명시된 기존 한계). 이 컬럼으로 "지금 이 세션이 어떤 이벤트에 대한
확인 질문을 냈는지" 세션 단위로 추적해, 다음 유저 발화를 그 확인에 대한 답으로
해석할 수 있게 한다(app/services/interview_service.py 참조).

Revision ID: 008
Revises: 007
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column(
            "pending_ocr_confirmation_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
            comment="OCR 확인 질문을 낸 대상 Event. 응답 처리 후 null로 되돌아간다.",
        ),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "pending_ocr_confirmation_event_id")
