"""고정 인터뷰 질문 100개 시드 (유년기~노년기)

기획자가 확정한 생애주기별 질문 문구를 questions 테이블에 채운다. 실제 문구는
app/data/question_bank.py(단일 원본, mock 스토어와 공유)에 있다.

Revision ID: 006
Revises: 005
Create Date: 2026-07-12
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID

from app.data.question_bank import QUESTION_BANK

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

# life_period는 001_initial_schema.py가 만든 네이티브 Postgres ENUM 타입(lifeperiod)이다
# — sa.String으로 바인딩하면 asyncpg가 "column is of type lifeperiod but expression is
# of type character varying"로 거부한다(001의 t_lifeperiod와 동일하게 create_type=False로
# 기존 타입을 그대로 참조).
t_lifeperiod = PG_ENUM(
    "childhood", "youth", "adulthood", "senior",
    name="lifeperiod", create_type=False,
)

questions_table = sa.table(
    "questions",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("sequence_order", sa.Integer),
    sa.column("title", sa.String),
    sa.column("content", sa.Text),
    sa.column("life_period", t_lifeperiod),
)


def upgrade() -> None:
    op.bulk_insert(
        questions_table,
        [
            {
                "id": uuid.uuid4(),
                "sequence_order": q["sequence_order"],
                "title": q["title"],
                "content": q["content"],
                "life_period": q["life_period"],
            }
            for q in QUESTION_BANK
        ],
    )


def downgrade() -> None:
    op.execute(
        questions_table.delete().where(
            questions_table.c.sequence_order.in_(
                [q["sequence_order"] for q in QUESTION_BANK]
            )
        )
    )
