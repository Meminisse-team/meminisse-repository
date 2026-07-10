import uuid

from app.clients import supabase_auth
from app.gateways.dto import UserCreateData, UserRecord
from app.gateways.factory import Gateways
from app.schemas.user import UserCreate


class EmailAlreadyRegisteredError(Exception):
    """이미 auth.users에 같은 이메일이 존재하는 경우(app/clients/supabase_auth.py의
    SupabaseAuthError(409, ...)를 서비스 레이어 예외로 변환)."""


async def create_user(gateways: Gateways, payload: UserCreate) -> UserRecord:
    """회원가입. 계정 생성 자체는 Supabase Auth Admin API가 담당하고(비밀번호를
    이 프로젝트가 저장하지 않기 위함), 그 결과로 받은 id를 그대로 이 프로젝트의
    public.users 프로필 행에 쓴다 — 두 단계가 하나라도 실패하면 부분 상태가
    남지 않도록 순서를 지킨다: auth.users 생성이 실패하면 public.users를 아예
    만들지 않고, public.users 생성이 실패하면(이론상 거의 없음 — id 충돌 등)
    auth.users만 고아로 남는다는 한계가 있다(TODO: 실패 시 auth.users 롤백/삭제
    로직은 이번 작업 범위 밖 — 발생 빈도가 매우 낮고, Supabase 대시보드에서
    수동 정리 가능)."""
    try:
        auth_user_id = await supabase_auth.admin_create_user(
            email=payload.email,
            password=payload.password,
            user_metadata={"name": payload.name},
        )
    except supabase_auth.SupabaseAuthError as exc:
        if exc.status_code == 409:
            raise EmailAlreadyRegisteredError() from exc
        raise

    user = await gateways.users.create(
        UserCreateData(
            id=auth_user_id,
            email=payload.email,
            name=payload.name,
            birth_year=payload.birth_year,
            hometown=payload.hometown,
        )
    )
    await gateways.commit()
    return user


async def get_user(gateways: Gateways, user_id: uuid.UUID) -> UserRecord | None:
    return await gateways.users.get_by_id(user_id)


async def get_user_by_email(gateways: Gateways, email: str) -> UserRecord | None:
    return await gateways.users.get_by_email(email)
