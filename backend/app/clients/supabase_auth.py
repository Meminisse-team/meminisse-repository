"""
Supabase Auth(GoTrue) REST API 클라이언트.

이 프로젝트는 자체 비밀번호 해싱/JWT 발급을 만드는 대신, Supabase 프로젝트에 이미
프로비저닝되어 있는 인증 서비스를 그대로 쓴다 — 2026-07-09 DB 실연동 검증 중
`auth`/`storage`/`realtime` 스키마가 이미 존재함을 확인했다(`auth.users` 등, 당시
0행). Supabase Auth를 쓰면 이메일 인증·비밀번호 재설정·소셜 로그인이 기본 제공되고,
이 프로젝트 DB에는 비밀번호 관련 값을 전혀 저장하지 않아도 된다.

app/clients/solar.py, embeddings.py, document_parse.py와 같은 패턴이다 — 순수 HTTP
래퍼이며 app/gateways/* 추상화 대상이 아니다(Gateway 패턴은 이 프로젝트의 자체 DB
테이블만 mock/postgres로 바꿔치기하기 위한 것이고, Supabase Auth는 그 스위치와
무관하게 항상 실제 서비스를 호출하는 외부 API다 — GATEWAY_BACKEND=mock으로 로컬을
띄워도 회원가입/로그인만큼은 실제 네트워크 호출이 된다). 테스트에서는
`app.clients.solar.chat_completion`을 모킹하는 기존 관례와 동일하게, 이 모듈의
함수들을 직접 패치한다(`tests/test_auth.py` 참조).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from app.config import settings

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class SupabaseAuthError(Exception):
    """Supabase Auth가 4xx를 반환한 모든 경우. status_code로 원인을 구분한다
    (409=이메일 중복, 401=자격증명 불일치, 그 외=예상 밖 오류)."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


def _admin_headers() -> dict[str, str]:
    """관리자 전용 엔드포인트(/admin/*)는 service_role 키를 apikey와 Authorization
    양쪽에 모두 요구한다(Supabase 문서 규약)."""
    return {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def _anon_headers() -> dict[str, str]:
    return {"apikey": settings.SUPABASE_ANON_KEY, "Content-Type": "application/json"}


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    return body.get("msg") or body.get("message") or body.get("error_description") or response.text


async def admin_create_user(*, email: str, password: str, user_metadata: dict[str, Any]) -> UUID:
    """관리자 권한으로 계정을 즉시 생성한다(`email_confirm=True`로 이메일 인증
    절차를 건너뛴다 — 이 프로젝트는 아직 이메일 발송 인프라가 없어 인증 메일
    자체를 보낼 수 없다. SMTP 연동 후에는 이 플래그를 재검토할 것).

    반환값은 `auth.users.id`. 이 값을 그대로 `public.users.id`로 사용해 두 테이블을
    1:1로 묶는다(alembic 004의 FK 제약, `app/services/user_service.py` 참조) —
    여기서 새 UUID를 만들지 않는다.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers=_admin_headers(),
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": user_metadata,
            },
        )
    if response.status_code in (400, 422):
        message = _error_message(response)
        if "already" in message.lower() or "exists" in message.lower() or "registered" in message.lower():
            raise SupabaseAuthError(409, "이미 등록된 이메일입니다.")
        raise SupabaseAuthError(response.status_code, message)
    response.raise_for_status()
    return UUID(response.json()["id"])


async def sign_in_with_password(*, email: str, password: str) -> dict[str, Any]:
    """성공 시 {"access_token", "refresh_token", "expires_in", "token_type", "user": {...}}."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            f"{settings.SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers=_anon_headers(),
            json={"email": email, "password": password},
        )
    if response.status_code in (400, 401):
        raise SupabaseAuthError(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
    response.raise_for_status()
    return response.json()


async def refresh_access_token(*, refresh_token: str) -> dict[str, Any]:
    """성공 시 sign_in_with_password와 동일한 형태(access_token 재발급 + 새 refresh_token)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            f"{settings.SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "refresh_token"},
            headers=_anon_headers(),
            json={"refresh_token": refresh_token},
        )
    if response.status_code in (400, 401):
        raise SupabaseAuthError(401, "리프레시 토큰이 유효하지 않습니다. 다시 로그인해 주세요.")
    response.raise_for_status()
    return response.json()
