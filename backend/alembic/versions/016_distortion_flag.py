"""왜곡 탐지 실패 세션 플래그: interview_sessions.distortion_flagged 추가

Phase 2 후처리에서 산문 재조립본이 NLI 왜곡 탐지를 (재시도 포함) 통과하지 못하면,
지금까지는 이벤트 추출을 조용히 보류만 하고 아무 흔적도 남기지 않았다 — 사용자도
관리자도 그 세션의 이야기가 자서전 재료에서 빠져 있다는 사실을 알 길이 없었다
(event_extraction_service의 오랜 TODO). 이 컬럼이 True인 세션은 '나의 이야기'
카드에 "AI 정리가 원문과 다를 수 있어요" 배지로 노출되고, 사용자가 산문을 직접
확인·수정해 저장하면(사람이 확정한 텍스트) 플래그가 해제되며 이벤트가 정상
추출된다(2026-07-18).

Revision ID: 016
Revises: 015
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column("distortion_flagged", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "distortion_flagged")
