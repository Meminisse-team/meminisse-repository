import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import InvalidTokenError, decode_access_token, decode_access_token_payload
from app.gateways.dto import UserRecord
from app.gateways.factory import Gateways, get_gateways
from app.services import auth_service
from app.services.auth_service import InvalidCredentialsError

GatewaysDep = Annotated[Gateways, Depends(get_gateways)]

# auto_error=True: Authorization 헤더 자체가 없으면 FastAPI가 자동으로 403을 던진다
# (엄밀히는 401이 더 맞지만 HTTPBearer의 기본 동작이 403이다 — 실제 검증 실패는
# 아래 get_current_user에서 명시적으로 401을 던지므로, 이 라이브러리 기본 403은
# "토큰을 아예 안 보낸" 경우에만 발생한다).
_bearer_scheme = HTTPBearer(description="로그인(POST /api/v1/auth/login) 응답의 access_token을 그대로 사용")


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
    gateways: GatewaysDep,
) -> UserRecord:
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "인증 토큰이 유효하지 않거나 만료되었습니다. 다시 로그인해 주세요.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        return await auth_service.get_current_user_or_raise(gateways, user_id)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "계정을 찾을 수 없습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUserDep = Annotated[UserRecord, Depends(get_current_user)]


async def get_verified_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
) -> dict:
    """get_current_user와 달리 public.users 프로필 조회를 하지 않는다 — 토큰
    자체(서명·만료·aud)만 검증하고 클레임 전체를 그대로 돌려준다. 소셜 로그인
    (OAuth) 첫 콜백 시점에는 auth.users는 이미 있어도 아직 public.users 프로필이
    없는 게 정상이므로(app/api/v1/auth.py의 POST /auth/oauth-sync가 바로 이
    시점에 프로필을 만든다), 그 엔드포인트는 CurrentUserDep을 쓸 수 없다."""
    try:
        return decode_access_token_payload(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "인증 토큰이 유효하지 않거나 만료되었습니다. 다시 로그인해 주세요.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


VerifiedTokenPayloadDep = Annotated[dict, Depends(get_verified_token_payload)]


def require_self(current_user: UserRecord, target_user_id: uuid.UUID) -> None:
    """target_user_id(경로 파라미터로 받은 user_id)가 요청자 본인 소유가 아니면
    403으로 거부한다. 라우터들이 "이 유저 리소스의 소유자가 나인가"를 확인하는
    공통 지점 — 존재 자체를 숨길 필요는 없는 자기 자신의 user_id 비교라 404 대신
    403을 쓴다(다른 사람의 세션/자서전처럼 하위 리소스를 열람하려는 시도는 각
    라우터가 자원을 조회한 뒤 404로 응답해 존재 여부 자체를 숨긴다)."""
    if current_user.id != target_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "본인의 리소스만 접근할 수 있습니다.")
