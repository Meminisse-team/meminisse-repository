"""Phase 5 실물 출판: autobiographies에 pdf_url 추가

Jinja2+WeasyPrint로 조판한 국판(A5) PDF를 S3에 올린 뒤 그 URL을 저장하는 필드.
final_content가 채워진 뒤에만(즉 최종 윤문 완료 후에만) 채워진다
(app/services/pdf_service.py:generate_manuscript_pdf 참조).

Revision ID: 005
Revises: 004
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "autobiographies",
        sa.Column(
            "pdf_url", sa.String(2048), nullable=True,
            comment="Jinja2+WeasyPrint로 조판한 국판(A5) PDF의 S3 URL. POD 발주 연계는 범위 밖.",
        ),
    )


def downgrade() -> None:
    op.drop_column("autobiographies", "pdf_url")
