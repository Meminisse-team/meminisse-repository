"""
로그인 + 토큰 갱신 + "내 정보" 조회. 회원가입은 여기가 아니라 POST /api/v1/users다
(app/schemas/user.py UserCreate 참조) — REST 관례상 "유저 생성"이 곧 가입이므로
별도 /auth/signup을 중복으로 두지 않았다.

실제 자격증명 검증·토큰 발급은 전부 Supabase Auth가 담당한다
(app/services/auth_service.py, app/clients/supabase_auth.py) — 이 라우터는 HTTP
계약만 정의한다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.user import UserRead
from app.services import auth_service
from app.services.auth_service import InvalidCredentialsError

router = APIRouter(prefix="/auth", tags=["auth"])


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
