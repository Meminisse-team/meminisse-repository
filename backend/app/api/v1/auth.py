"""
로그인 + 토큰 갱신 + "내 정보" 조회. 회원가입은 여기가 아니라 POST /api/v1/users다
(app/schemas/user.py UserCreate 참조) — REST 관례상 "유저 생성"이 곧 가입이므로
별도 /auth/signup을 중복으로 두지 않았다.

실제 자격증명 검증·토큰 발급은 전부 Supabase Auth가 담당한다
(app/services/auth_service.py, app/clients/supabase_auth.py) — 이 라우터는 HTTP
계약만 정의한다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, GatewaysDep, VerifiedTokenPayloadDep
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.user import OAuthSyncResponse, UserRead
from app.services import auth_service, user_service
from app.services.auth_service import InvalidCredentialsError

router = APIRouter(prefix="/auth", tags=["auth"])

# Supabase가 소셜 로그인 제공자별로 user_metadata에 표시 이름을 채우는 키가
# 제각각이라(구글은 보통 full_name/name, 카카오는 name 또는 nickname) 순서대로
# 시도하고 전부 없으면 이메일 로컬파트로 대체한다.
_OAUTH_NAME_METADATA_KEYS = ("name", "full_name", "nickname", "preferred_username")


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    try:
        return await auth_service.login(payload)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다."
        ) from exc


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest) -> TokenResponse:
    """`access_token`이 만료된 뒤(기본 1시간, Supabase 프로젝트 설정에 따름) 재로그인
    없이 새 토큰 쌍을 받는 엔드포인트. `refresh_token`은 로그인 응답에 함께 온다."""
    try:
        return await auth_service.refresh(payload.refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "리프레시 토큰이 유효하지 않거나 만료되었습니다. 다시 로그인해 주세요."
        ) from exc


@router.get("/me", response_model=UserRead)
async def get_me(current_user: CurrentUserDep) -> UserRead:
    return UserRead.model_validate(current_user)


@router.post("/oauth-sync", response_model=OAuthSyncResponse)
async def oauth_sync(payload: VerifiedTokenPayloadDep, gateways: GatewaysDep) -> OAuthSyncResponse:
    """소셜 로그인(Kakao/Google) 콜백 직후 프론트가 호출한다.

    이메일/비밀번호 가입과 달리 이 시점엔 이미 Supabase가 auth.users 계정을
    만들어버린 뒤라(OAuth 동의 화면에서 승인하는 순간 생성됨), 이 프로젝트가
    "가입"을 트리거할 수 없다 — 대신 세션 토큰만으로 프로필 존재를 확인/보장한다.
    CurrentUserDep을 쓰지 않는 이유: 그 의존성은 public.users가 없으면 401을
    던지는데, 최초 로그인 시점엔 정확히 그 상태(auth.users는 있고 public.users는
    아직 없음)가 정상이라 VerifiedTokenPayloadDep(토큰 검증만, 프로필 조회 없음)
    을 쓴다."""
    user_id = uuid.UUID(payload["sub"])
    email = payload.get("email") or ""
    metadata = payload.get("user_metadata") or {}
    name = next(
        (metadata[key] for key in _OAUTH_NAME_METADATA_KEYS if metadata.get(key)),
        email.split("@")[0] if email else "사용자",
    )
    user, is_new = await user_service.sync_oauth_user(
        gateways, user_id=user_id, email=email, name=name
    )
    return OAuthSyncResponse(user=UserRead.model_validate(user), is_new=is_new)
