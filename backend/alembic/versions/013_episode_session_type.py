"""SessionType에 'episode' 값 추가 — 자유 에피소드 세션

대시보드 시작 화면의 "에피소드 추가"(2026-07-16): 자동 배정 큐(고정 질문/사진)와
무관하게 사용자가 직접 시작하는 세션. FIXED_QUESTION을 question_id=null로
재사용하지 않는 이유는 interview_service.create_session()의 자동 배정 분기가
정확히 (question_id is None and linked_media_asset_id is None and
session_type == FIXED_QUESTION)일 때 다음 큐 항목을 자동으로 배정하기 때문 —
재사용하면 "빈 에피소드"가 아니라 다음 고정 질문이 조용히 배정된다. 별도 enum
값으로 이 경로를 원천적으로 피한다(app/services/interview_service.py 참조).

Postgres enum은 새 라벨을 CREATE TYPE으로 다시 만들 수 없고 ALTER TYPE ... ADD
VALUE로만 추가할 수 있다. Postgres 12+에서는 트랜잭션 내에서 실행 가능하지만,
같은 트랜잭션 안에서 그 새 라벨을 곧바로 DML에 사용할 수는 없다 — 이 마이그레이션은
라벨 추가만 하므로 문제없다.

downgrade()는 지원하지 않는다: Postgres는 enum에서 라벨 하나만 제거하는 기능이
없다(타입 전체를 새로 만들어 모든 참조 컬럼을 재작성해야 하는데, 이 프로젝트의
다른 마이그레이션에 그런 다운그레이드 선례가 없고 이번 변경의 스코프를 벗어난다).

Revision ID: 013
Revises: 012
Create Date: 2026-07-16
"""

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE sessiontype ADD VALUE IF NOT EXISTS 'episode'")


def downgrade() -> None:
    raise NotImplementedError(
        "Postgres는 enum 라벨 하나만 제거할 수 없다 (sessiontype에서 'episode' 제거 불가). "
        "되돌리려면 새 타입을 만들어 컬럼을 재작성해야 하며 이 마이그레이션 범위 밖이다."
    )
