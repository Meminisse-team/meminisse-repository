from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    """Supabase Auth가 발급한 세션을 그대로 전달한다(app/clients/supabase_auth.py)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # 초 단위. access_token 만료 후에는 refresh_token으로 /auth/refresh 호출.
