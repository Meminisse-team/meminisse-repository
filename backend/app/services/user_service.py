import uuid

from app.clients import supabase_auth
from app.gateways.dto import UserCreateData, UserRecord
from app.gateways.factory import Gateways
from app.schemas.user import UserCreate


class EmailAlreadyRegisteredError(Exception):
    """이미 auth.users에 같은 이메일이 존재하는 경우(app/clients/supabase_auth.py의
    SupabaseAuthError(409, ...)를 서비스 레이어 예외로 변환)."""


class InvalidSignupError(Exception):
    """이메일 중복이 아닌 다른 이유로 Supabase Auth가 계정 생성을 거부한 경우
    (예: 프로젝트 비밀번호 정책 위반, 429 요청 제한, Supabase 측 5xx 등). 라우터가
    이 예외를 못 잡으면 예전에는 그대로 500으로 새어나갔다 — 클라이언트가 가입
    실패 이유를 알 수 있도록 400으로 매핑한다(app/api/v1/users.py 참조)."""


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
        raise InvalidSignupError(str(exc)) from exc

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


async def sync_oauth_user(
    gateways: Gateways, *, user_id: uuid.UUID, email: str, name: str
) -> tuple[UserRecord, bool]:
    """소셜 로그인(Kakao/Google 등 OAuth) 첫 콜백에서 호출된다.

    비밀번호 기반 가입(create_user)과 달리 auth.users 행은 이미 Supabase가 OAuth
    핸드셰이크 도중 만들어버린 뒤라, 이 함수가 호출되는 시점엔 우리가 관여할 여지
    없이 계정이 이미 존재한다 — 여기서는 그 id로 public.users 프로필이 있는지만
    확인하고, 없으면(=이 서비스 최초 로그인) 만든다. admin_create_user를 호출하지
    않는다는 점이 create_user와의 유일한 차이다.

    OAuth는 이메일/비밀번호 가입과 달리 "생년/고향/동의까지 한 번에 받은 뒤 계정을
    만드는" 지연 생성이 불가능하다(제공자가 동의하는 순간 즉시 계정이 생겨버림) —
    그래서 여기서는 이메일·이름만으로 최소 프로필을 만들고, 생년/고향은 프론트가
    로그인 직후 별도 화면에서 PATCH /users/{id}(update_profile)로 채운다.

    반환값의 두 번째 원소(is_new)로 프론트가 "이번이 최초 로그인인지"를 판단해
    프로필 완성 단계로 보낼지 바로 대시보드로 보낼지 분기한다."""
    existing = await gateways.users.get_by_id(user_id)
    if existing is not None:
        return existing, False

    user = await gateways.users.create(
        UserCreateData(id=user_id, email=email, name=name)
    )
    await gateways.commit()
    return user, True


async def update_profile(
    gateways: Gateways,
    user_id: uuid.UUID,
    *,
    name: str | None = None,
    birth_year: int | None = None,
    hometown: str | None = None,
) -> UserRecord:
    user = await gateways.users.update(
        user_id, name=name, birth_year=birth_year, hometown=hometown
    )
    await gateways.commit()
    return user
