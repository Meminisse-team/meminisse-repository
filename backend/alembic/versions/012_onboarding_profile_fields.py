"""가입 시 명시 입력 프로필: users.education_level / marital_status / has_children

동적 질문 필터링(사용자에게 맞지 않는 질문을 사전에 건너뛰는 기능)을 위해 대화
내용을 추론하는 대신, 가입 온보딩에서 라디오 버튼으로 직접 입력받는 방식으로
설계했다(2026-07-16 방향 전환 — 대화 기반 사실 추출은 채택하지 않음). 셋 다
선택 응답이라 전부 nullable이고, null은 "모름"과 동일하게 취급되어 그 정보를
전제로 한 질문도 필터링 없이 정상적으로 나간다(app/data/question_bank.py의
eligibility, app/services/interview_service.py의 필터링 로직 참조).

Revision ID: 012
Revises: 011
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE educationlevel AS ENUM "
        "('elementary', 'middle_school', 'high_school', 'university', 'graduate_school')"
    )
    op.execute("CREATE TYPE maritalstatus AS ENUM ('single', 'married', 'divorced', 'widowed')")

    t_educationlevel = PG_ENUM(
        "elementary", "middle_school", "high_school", "university", "graduate_school",
        name="educationlevel", create_type=False,
    )
    t_maritalstatus = PG_ENUM(
        "single", "married", "divorced", "widowed", name="maritalstatus", create_type=False,
    )

    op.add_column(
        "users",
        sa.Column(
            "education_level", t_educationlevel, nullable=True,
            comment="가입 시 라디오 버튼 입력. null = 응답하지 않음(모름과 동일 취급).",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "marital_status", t_maritalstatus, nullable=True,
            comment="가입 시 라디오 버튼 입력. null = 응답하지 않음(모름과 동일 취급).",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "has_children", sa.Boolean, nullable=True,
            comment="가입 시 라디오 버튼 입력. null = 응답하지 않음(모름과 동일 취급).",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "has_children")
    op.drop_column("users", "marital_status")
    op.drop_column("users", "education_level")
    op.execute("DROP TYPE maritalstatus")
    op.execute("DROP TYPE educationlevel")
