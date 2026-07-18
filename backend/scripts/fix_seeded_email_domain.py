"""
scripts/seed_dummy.py --email에 .local 등 특수 예약 도메인(RFC 6762 mDNS 등)을
쓰면 시딩·Phase 2 처리 자체는 문제없이 끝나지만, 프론트엔드 로그인
(POST /api/v1/auth/login, app/schemas/auth.py:LoginRequest.email: EmailStr)이
Pydantic의 email-validator에 막혀 422를 반환한다 — email-validator가 문법
검증 단계에서 특수 용도 도메인을 거부하기 때문이다(실측 확인, 2026-07-18).
DNS 조회(존재하지 않는 도메인인지)는 검사하지 않으므로 example.com/eval.dev
같은 가짜지만 "진짜 TLD" 형태의 도메인은 문제없다.

이 스크립트는 이미 시딩·처리된 계정을 재시딩(1~2단계 반복, 이벤트 추출
API 비용 재발생) 없이 이메일만 유효한 도메인으로 바꾼다 — auth.users와
public.users 양쪽을 user_id(UUID) 기준으로 갱신하므로 이미 처리된 세션·이벤트
데이터는 그대로 유지된다.

실행 (backend/ 디렉토리에서):
    ..\\venv\\Scripts\\python -m scripts.fix_seeded_email_domain \\
        --old-email "ulysses_s_grant@eval.local" --new-email "ulysses_s_grant@eval.dev"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import httpx

from app.config import settings
from app.gateways.factory import gateways_context
from scripts.seed_dummy import _admin_headers, _TIMEOUT


async def _find_auth_user_id(email: str) -> uuid.UUID | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers=_admin_headers(),
            params={"page": 1, "per_page": 1000},
        )
        resp.raise_for_status()
        data = resp.json()
        users_list = data.get("users", data) if isinstance(data, dict) else data
        for u in users_list:
            if u.get("email", "").lower() == email.lower():
                return uuid.UUID(u["id"])
    return None


async def _update_auth_email(user_id: uuid.UUID, new_email: str) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.put(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers=_admin_headers(),
            json={"email": new_email, "email_confirm": True},
        )
        resp.raise_for_status()


async def main(old_email: str, new_email: str) -> None:
    user_id = await _find_auth_user_id(old_email)
    if user_id is None:
        print(f"❌ auth.users에서 {old_email}을 찾지 못했습니다.")
        sys.exit(1)
    print(f"[Auth] {old_email} → {new_email} (user_id={user_id})")

    await _update_auth_email(user_id, new_email)
    print("  [Auth] auth.users 이메일 갱신 완료")

    async with gateways_context() as gateways:
        await gateways.users.update_email(user_id, new_email)
        await gateways.commit()
    print("  [DB] public.users 이메일 갱신 완료")
    print(f"\n✅ 완료 — 이제 {new_email}로 로그인 가능합니다(비밀번호는 변경 없음).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="이미 시딩된 계정의 이메일을 재시딩 없이 유효한 도메인으로 바꿉니다.")
    parser.add_argument("--old-email", required=True, help="현재 이메일(.local 등 특수 도메인)")
    parser.add_argument("--new-email", required=True, help="바꿀 이메일(예: ulysses_s_grant@eval.dev)")
    args = parser.parse_args()
    asyncio.run(main(args.old_email, args.new_email))
