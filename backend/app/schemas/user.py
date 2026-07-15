import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import EducationLevel, MaritalStatus, UserRole, UserStage


class UserCreate(BaseModel):
    """회원가입 요청. POST /api/v1/users가 곧 가입 엔드포인트다(별도 /auth/signup을
    두지 않았다 — REST 리소스 생성 관례상 "유저를 만드는 것"과 "가입"은 같은 동작이므로
    분리가 오히려 혼란을 준다). 로그인은 이메일+비밀번호로 별도 POST /api/v1/auth/login.

    `password`는 이 서버가 직접 저장하지 않는다 — Supabase Auth Admin API로 그대로
    전달되어 auth.users 생성에만 쓰이고, 이후 이 프로젝트 코드/DB 어디에도 남지
    않는다(app/services/user_service.py, app/clients/supabase_auth.py 참조).

    education_level/marital_status/has_children은 온보딩에서 라디오 버튼으로 직접
    입력받는 선택 응답이다(2026-07-16 설계 — 대화 내용 추론 대신 명시적 입력으로
    동적 질문 필터링을 판정). 안 보내면(None) "응답하지 않음"과 동일하게 취급되어
    그 정보를 전제로 한 질문도 필터링 없이 정상적으로 나간다."""

    email: EmailStr
    name: str
    password: str = Field(..., min_length=8, description="평문 비밀번호. Supabase Auth로 그대로 전달되며 이 서버에는 저장되지 않는다.")
    birth_year: int | None = None
    hometown: str | None = None
    education_level: EducationLevel | None = None
    marital_status: MaritalStatus | None = None
    has_children: bool | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    name: str
    birth_year: int | None
    hometown: str | None
    current_stage: UserStage
    role: UserRole
    education_level: EducationLevel | None
    marital_status: MaritalStatus | None
    has_children: bool | None


class UserProfileUpdate(BaseModel):
    """PATCH /api/v1/users/{user_id}. 소셜 로그인은 이메일/비밀번호 가입과 달리
    생년/고향을 계정 생성과 동시에 받을 수 없어(app/services/user_service.py:
    sync_oauth_user 참조) 로그인 직후 이 엔드포인트로 채운다 — 다만 일반 프로필
    수정에도 그대로 쓸 수 있도록 범용으로 둔다. 전부 선택 필드이며 보낸 값만 갱신.

    education_level/marital_status/has_children도 같은 "보낸 값만 갱신" 규칙을
    따른다 — 알려진 한계: 한 번 값을 넣은 뒤 "응답하지 않음"으로 되돌리는 건 이
    엔드포인트로는 표현할 수 없다(UserCreate 문서 및 UserGateway.update 문서 참조)."""

    name: str | None = None
    birth_year: int | None = None
    hometown: str | None = None
    education_level: EducationLevel | None = None
    marital_status: MaritalStatus | None = None
    has_children: bool | None = None


class OAuthSyncResponse(BaseModel):
    user: UserRead
    is_new: bool = Field(
        ..., description="이 서비스에 처음 로그인해 방금 프로필이 생성됐으면 true — 프론트가 이 값으로 프로필 완성 단계 진입 여부를 결정한다."
    )
