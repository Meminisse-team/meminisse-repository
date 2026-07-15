"""레거시 39문항 → 신규 100문항 전환 (기존 세션의 question_id FK를 깨지 않고)

006이 원래 39개 질문을 sequence_order 1~39로 심었는데, 이후 팀원이 app/data/
question_bank.py 내용을 100개로 통째로 교체했다(2026-07-15, 커밋 c07ce9a). Alembic은
리비전 파일의 코드 diff가 아니라 "이 리비전 ID가 이미 적용됐는가"만 추적하므로, 006을
고친 것만으로는 이미 006을 실행해버린 기존 DB의 questions 테이블이 자동으로 갱신되지
않는다 — 이 문제 자체는 팀원 인수인계 메시지가 정확히 짚었다.

다만 팀원이 제안한 "questions 비우고 006을 alembic downgrade -1 && upgrade head로
재실행" 방식은 이 프로젝트의 실제 스키마/데이터 상태에서 그대로 쓸 수 없다는 것을
확인했다(2026-07-16):

1. questions.sequence_order가 UNIQUE 제약이다(models/question.py). 새 100문항도
   전역 연속 번호 1~100을 그대로 쓰므로(app/data/question_bank.py 주석 참조), 기존
   1~39번과 정면으로 충돌해 단순 삽입이 유니크 제약 위반으로 실패한다.
2. interview_sessions.question_id가 questions.id를 참조하는 FK인데 001_initial_
   schema.py가 ondelete를 지정하지 않아 기본값 RESTRICT다. 이 DB를 실제로 조회해보니
   기존 39문항 중 10개를 참조하는 세션이 이미 존재한다(2026-07-16 확인) — 006의
   downgrade()로 그 행을 지우려 하면 FK 위반으로 트랜잭션 전체가 실패한다. 게다가
   006의 downgrade()는 "현재(수정된) QUESTION_BANK의 sequence_order 목록"을 삭제
   기준으로 삼으므로, 코드가 100문항으로 바뀐 지금 실행하면 애초에 옛 39개가 아니라
   엉뚱한(아직 존재하지도 않는) 신규 sequence_order를 대상으로 삭제를 시도해 의도한
   대로 동작하지도 않는다.

2026-07-16 추가 확인: 팀원이 사진 분석(Azure Vision 전환) 작업에서 media_assets
컬럼 추가 마이그레이션을 010번으로 먼저 push했다 — 이 마이그레이션도 리비전 ID를
010으로 잡아뒀던 터라 그대로 두면 두 파일이 같은 revision을 주장하는 충돌이었다.
011로 옮기고 down_revision을 010(media_asset_vision_fields)으로 바꿔 체이닝한다 —
questions 테이블과 media_assets 테이블은 서로 무관해 로직 자체는 손댈 필요가 없었다.

그래서 이 마이그레이션은 기존 39개 행을 삭제하지 않는다. 대신:
- sequence_order를 충돌 없는 범위(+10000)로 밀어내고 is_active=False로 비활성화한다
  — 과거 세션의 question_id FK 참조는 그대로 유지되고, 질문 배정 로직(SqlAlchemy
  QuestionGateway.get_next_unanswered)은 is_active=True만 조회하므로 더 이상 새
  사용자에게 배정되지 않는다. autobiography_service.py의 QUESTION_BANK_BY_SEQUENCE
  조회는 존재하지 않는 sequence_order에 대해 None을 받아 조용히 건너뛰도록 이미
  작성돼 있어(옛 질문에 답한 세션의 태그 추천 신호가 약간 줄어드는 정도), 이 이동이
  안전하다.
- 신규 100문항을 sequence_order 1~100, is_active=True로 삽입한다. content가 이미
  테이블에 있는 행은 건너뛴다 — 이 마이그레이션을 실수로 두 번 적용해도 중복
  삽입되지 않게 하기 위함(완전한 범용 재실행 안전성을 노리는 건 아니고, 이 특정
  전환을 두 번 돌려도 최소한 안전하게 무해하도록 하는 정도의 방어선).

Revision ID: 011
Revises: 010
Create Date: 2026-07-16
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID

from app.data.question_bank import QUESTION_BANK

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

# 006과 동일한 이유로 기존 네이티브 ENUM 타입을 그대로 참조한다(create_type=False).
t_lifeperiod = PG_ENUM(
    "childhood", "youth", "adulthood", "senior",
    name="lifeperiod", create_type=False,
)

# 레거시 행을 옮겨 놓을 오프셋. 신규 100문항이 1~100을 쓰므로 그보다 충분히 크게 둔다.
_LEGACY_SEQUENCE_OFFSET = 10000

questions_table = sa.table(
    "questions",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("sequence_order", sa.Integer),
    sa.column("title", sa.String),
    sa.column("content", sa.Text),
    sa.column("life_period", t_lifeperiod),
    sa.column("is_active", sa.Boolean),
)


def upgrade() -> None:
    bind = op.get_bind()
    new_contents = [q["content"] for q in QUESTION_BANK]

    # 1) 현재 QUESTION_BANK에 없는(=레거시) 행을 충돌 없는 범위로 밀어내고 비활성화.
    bind.execute(
        questions_table.update()
        .where(questions_table.c.content.notin_(new_contents))
        .where(questions_table.c.sequence_order < _LEGACY_SEQUENCE_OFFSET)
        .values(
            sequence_order=questions_table.c.sequence_order + _LEGACY_SEQUENCE_OFFSET,
            is_active=False,
        )
    )

    # 2) 신규 100문항 중 아직 없는 것만 삽입(재실행 방어).
    existing_contents = {
        row[0] for row in bind.execute(sa.select(questions_table.c.content)).fetchall()
    }
    to_insert = [q for q in QUESTION_BANK if q["content"] not in existing_contents]
    if to_insert:
        op.bulk_insert(
            questions_table,
            [
                {
                    "id": uuid.uuid4(),
                    "sequence_order": q["sequence_order"],
                    "title": q["title"],
                    "content": q["content"],
                    "life_period": q["life_period"],
                    "is_active": True,
                }
                for q in to_insert
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    new_contents = [q["content"] for q in QUESTION_BANK]

    # 이 마이그레이션이 삽입한 신규 100문항을 지운다 — 적용 직후 곧바로 되돌리는
    # 경우만 안전하다(그 사이 새 사용자가 이 질문들로 세션을 이미 만들었다면 FK
    # 위반으로 여기서 막힌다 — 의도된 안전장치).
    bind.execute(questions_table.delete().where(questions_table.c.content.in_(new_contents)))

    # 밀어냈던 레거시 행을 원래 sequence_order/is_active로 복구.
    bind.execute(
        questions_table.update()
        .where(questions_table.c.sequence_order >= _LEGACY_SEQUENCE_OFFSET)
        .values(
            sequence_order=questions_table.c.sequence_order - _LEGACY_SEQUENCE_OFFSET,
            is_active=True,
        )
    )
