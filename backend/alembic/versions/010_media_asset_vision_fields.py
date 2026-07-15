"""사진 캡션/텍스트 인식 결과 컬럼 추가 (Azure Vision 전환)

기존 Upstage Document Parse(텍스트만 읽는 OCR)를 Azure AI Vision Image Analysis로
교체하면서(app/clients/azure_vision.py), 사진마다 캡션(image_caption)과 사진 속
텍스트(image_ocr_text)를 함께 저장한다. 둘 다 PHOTO 세션 오프닝 질문을 만드는
재료로 쓰인다(app/agents/prompts.py:build_photo_session_opening) — 캡션은 항상,
텍스트는 사진 속에 글자가 검출됐을 때만(analysis_track=TEXT_DOCUMENT) 채워진다.

Revision ID: 010
Revises: 009
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column(
            "image_caption", sa.Text, nullable=True,
            comment="Azure Vision 캡션 — 사진의 시각적 내용을 설명하는 한 문장.",
        ),
    )
    op.add_column(
        "media_assets",
        sa.Column(
            "image_ocr_text", sa.Text, nullable=True,
            comment="Azure Vision이 사진 속에서 읽어낸 인쇄/손글씨 텍스트. "
            "analysis_track=text_document일 때만 채워진다.",
        ),
    )


def downgrade() -> None:
    op.drop_column("media_assets", "image_ocr_text")
    op.drop_column("media_assets", "image_caption")
