"""
로그인/토큰갱신(Supabase Auth 위임) + 토큰으로부터 현재 유저 로드.

회원가입은 user_service.create_user가 담당한다(POST /api/v1/users) — 이 모듈은
"이미 존재하는 auth.users 계정으로 세션(JWT)을 발급/갱신받는" 절차와, 그 세션
토큰의 subject로 프로필(public.users)을 조회하는 절차만 다룬다. 실제 자격증명
검증(비밀번호 대조)은 이 프로젝트가 하지 않는다 — Supabase Auth가 전담한다.
"""

from __future__ import annotations

import uuid

from app.clients import supabase_auth
from app.gateways.dto import UserRecord
from app.gateways.factory import Gateways
from app.schemas.auth import LoginRequest, TokenResponse


class InvalidCredentialsError(Exception):
    """이메일/비밀번호 불일치, 리프레시 토큰 만료, 혹은 토큰은 유효하지만 그
    사이 계정이 삭제된 경우까지 전부 포함한다. 원인을 구분해 응답하지 않는다 —
    "이메일이 존재하지 않습니다" 식의 메시지는 계정 존재 여부를 외부에 노출하는
    사용자 열거(user enumeration) 공격의 단서가 되므로 항상 동일한 401로
    매핑한다(라우터 참조)."""


def _to_token_response(session: dict) -> TokenResponse:
    return TokenResponse(
        access_token=session["access_token"],
        refresh_token=session["refresh_token"],
        expires_in=session["expires_in"],
    )


async def login(payload: LoginRequest) -> TokenResponse:
    """gateways를 받지 않는다 — 자격증명 검증은 Supabase Auth가 전담하므로 이
    프로젝트 DB를 조회할 필요가 없다(get_current_user_or_raise와의 비대칭은
    의도된 것: 로그인은 "계정이 맞는지"만 확인하고, 이후 매 요청의
    get_current_user는 "그 계정의 프로필이 우리 DB에도 있는지"까지 확인한다)."""
    try:
        session = await supabase_auth.sign_in_with_password(
            email=payload.email, password=payload.password
        )
    except supabase_auth.SupabaseAuthError as exc:
        raise InvalidCredentialsError() from exc
    return _to_token_response(session)


async def refresh(refresh_token: str) -> TokenResponse:
    try:
        session = await supabase_auth.refresh_access_token(refresh_token=refresh_token)
    except supabase_auth.SupabaseAuthError as exc:
        raise InvalidCredentialsError() from exc
    return _to_token_response(session)


async def get_current_user_or_raise(gateways: Gateways, user_id: uuid.UUID) -> UserRecord:
    """get_current_user 의존성(app/api/deps.py)이 액세스 토큰 디코딩 후 호출한다.
    토큰 자체는 유효하지만(서명·만료 통과) public.users 프로필이 없는 경우(계정
    삭제 직후의 만료 전 토큰 재사용 등)를 위한 방어."""
    user = await gateways.users.get_by_id(user_id)
    if user is None:
        raise InvalidCredentialsError()
    return user
