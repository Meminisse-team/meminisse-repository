"""자서전 다중 버전 지원: user_id 유니크 제약 제거

기존엔 유저당 자서전 1권으로 고정돼 있었다(uq_autobiographies_user_id). 완성된
버전을 "나의 책장"에 남겨두고 새 버전을 또 시작할 수 있게 하려면 이 제약부터
없애야 한다(2026-07-17). 유니크 제약을 일반 인덱스로 바꿔 user_id 조회 성능은
그대로 유지한다.

get_or_create_autobiography(app/services/autobiography_service.py)는 이제
"이 유저의 미완성(final_content IS NULL) 자서전 중 최신 것"을 찾고, 없으면(모두
완성됐거나 하나도 없으면) 새로 만든다 — 그래서 완성 후 "자서전 집필"에 다시
들어가면 자동으로 새 버전이 시작된다.

Revision ID: 015
Revises: 014
Create Date: 2026-07-17
"""

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_autobiographies_user_id", "autobiographies", type_="unique")
    op.create_index("ix_autobiographies_user_id", "autobiographies", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_autobiographies_user_id", table_name="autobiographies")
    op.create_unique_constraint("uq_autobiographies_user_id", "autobiographies", ["user_id"])
