"""Supabase Auth 연동: public.users를 auth.users에 결속

기획안에는 없던 기능이지만, 다른 사람의 자서전·인터뷰·사진에 접근하지 못하도록
막으려면 "누가 요청했는지"를 서버가 알아야 한다. 자체 비밀번호 해싱 + JWT 발급으로
구현했던 첫 버전의 이 리비전을 곧바로 이 버전으로 재작성했다 — 이 Supabase
프로젝트에는 `auth`/`storage`/`realtime` 스키마가 이미 프로비저닝되어 있음을
DB 실연동 검증 중 확인했고(2026-07-09, `auth.users` 등 확인, 당시 0행), 자체
인증 체계를 새로 만드는 대신 Supabase Auth(GoTrue)를 그대로 쓰기로 했다. 이러면
이메일 인증·비밀번호 재설정·소셜 로그인을 직접 구현할 필요가 없고, 비밀번호 관련
값을 이 프로젝트 DB에 전혀 저장하지 않아도 된다.

`public.users.id`에 `auth.users(id)` FK(ON DELETE CASCADE)를 건다(Supabase
커뮤니티의 표준 "profiles" 패턴). 회원가입 시 서비스 레이어(app/services/
user_service.py)가 Supabase Auth Admin API로 `auth.users` 행을 먼저 만들고, 그
id를 그대로 `public.users.id`로 써서 이 프로필 행을 생성한다 — 즉 이 FK가 항상
만족되도록 애플리케이션 코드가 보장하며, DB 레벨 제약은 그 보장이 깨졌을 때(직접
SQL로 잘못된 id를 넣는 등)의 최종 방어선이다. ON DELETE CASCADE 덕분에 Supabase
대시보드나 Admin API로 계정을 삭제하면 이 프로젝트의 프로필/세션/이벤트/자서전
등 하위 데이터도 함께 정리된다(계정 삭제 시 개인정보를 남기지 않기 위함).

주의: `public.users.id`는 더 이상 이 프로젝트가 생성하지 않으므로(이전에는
SQLAlchemy 쪽 `default=uuid.uuid4`가 있었으나 제거했다, app/models/user.py 참조),
이 마이그레이션 적용 전에 이미 `id`가 `auth.users`에 없는 행이 `public.users`에
들어있다면(있을 수 없다고 확인했지만— 2026-07-09 기준 운영 Supabase는 0행) FK
생성 자체가 실패한다.

Revision ID: 004
Revises: 003
Create Date: 2026-07-10
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_users_auth_users",
        source_table="users",
        referent_table="users",
        local_cols=["id"],
        remote_cols=["id"],
        referent_schema="auth",
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_auth_users", "users", type_="foreignkey")
