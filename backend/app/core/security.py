"""
Supabase Auth가 발급한 세션 액세스 토큰(JWT)을 검증한다.

비밀번호 해싱과 토큰 발급은 이 프로젝트가 더 이상 직접 하지 않는다 — Supabase
Auth(GoTrue)가 전담하고(app/clients/supabase_auth.py), 여기서는 그 토큰이 우리
Supabase 프로젝트가 실제로 발급한 것이 맞는지(서명 검증)와 아직 만료되지 않았는지만
확인해 subject(= auth.users.id = public.users.id, app/models/user.py 참조)를 꺼낸다.

두 서명 방식을 모두 지원한다:
- HS256(대칭키, `SUPABASE_JWT_SECRET`) — 레거시 Supabase 프로젝트, 그리고
  네트워크 호출 없이 빠르게 도는 이 프로젝트의 테스트(tests/test_auth.py)가 사용.
- ES256/RS256 등 비대칭키 — 이 프로젝트가 실제로 쓰는 방식(2026-07-10 실제
  Supabase 연동 검증 중 발급된 토큰 헤더가 `{"alg": "ES256", ...}`임을 확인했다).
  `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`에서 공개키를 받아와 검증한다
  (PyJWKClient가 kid로 올바른 키를 골라내고, 최대 5분 캐시한다).

토큰 헤더의 `alg` 값으로 두 경로 중 하나를 고르되, 각 경로는 서로 다른 키
(우리만 아는 비밀 vs 공개키)로 독립적으로 검증하고 jwt.decode()에 그 경로에서만
허용할 알고리즘 한 개만 명시적으로 넘긴다 — "헤더의 alg를 신뢰하고 아무 키로나
검증"하는 것이 아니므로 이른바 alg-confusion 공격 패턴에 해당하지 않는다.
"""

from __future__ import annotations

import uuid
from functools import lru_cache

import jwt

from app.config import settings

_EXPECTED_AUDIENCE = "authenticated"  # Supabase 세션 토큰의 고정 aud 클레임
_HS_ALGORITHMS = {"HS256", "HS384", "HS512"}


class InvalidTokenError(Exception):
    """토큰이 없거나, 서명이 위조됐거나, 만료됐거나, subject가 UUID가 아닌 모든 경우."""


@lru_cache(maxsize=1)
def _jwk_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json")


def decode_access_token(token: str) -> uuid.UUID:
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg in _HS_ALGORITHMS:
            payload = jwt.decode(
                token, settings.SUPABASE_JWT_SECRET, algorithms=[alg], audience=_EXPECTED_AUDIENCE
            )
        else:
            signing_key = _jwk_client().get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token, signing_key.key, algorithms=[alg], audience=_EXPECTED_AUDIENCE
            )
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc
