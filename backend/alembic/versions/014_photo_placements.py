"""자서전 수록 사진 배치: autobiographies에 photo_placements 추가

PDF 조판 직전에 사용자가 "어떤 사진을 어느 챕터의 어느 슬롯에 넣을지"를 직접
고른 결과(2026-07-16 요구). 기획안 5절의 고정 슬롯 템플릿 원칙에 따라 위치는
자유 좌표가 아니라 {media_asset_id, chapter_index, slot, caption}의 배열로만
표현한다 — slot은 "chapter_top"(상단 이미지+하단 캡션형) 또는
"full_page_before"(챕터 앞 전면 사진 페이지형) 두 가지뿐이다.

값이 NULL(미지정)이든 빈 배열이든 조판 시 사진은 들어가지 않는다 — pdf_service는
사용자가 여기 지정한 사진만 넣는다(자동 선택 없음).

Revision ID: 014
Revises: 013
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "autobiographies",
        sa.Column(
            "photo_placements",
            postgresql.JSONB,
            nullable=True,
            comment=(
                "자서전 수록 사진 배치 지정 배열 [{media_asset_id, chapter_index, slot, caption}]."
                " NULL(미지정)/빈 배열 모두 조판 시 사진 없음 — 지정된 사진만 수록."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("autobiographies", "photo_placements")
