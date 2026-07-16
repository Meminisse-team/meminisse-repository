"""관리자 대시보드 응답 스키마. app/services/admin_service.py 참조."""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.interview import SessionRead
from app.schemas.user import UserRead

# 세션 요약 형태가 SessionRead와 동일해 별칭으로만 노출한다 — 관리자 뷰라고
# 필드가 달라질 이유가 없다(둘 다 개인정보인 chat_logs/session_prose는 제외).
AdminSessionRead = SessionRead


class AdminSessionDetailRead(SessionRead):
    """유저 상세 조회(GET /admin/users/lookup) 전용 — session_prose를 포함한다.
    관리자가 산문을 직접 읽고 고치려면 필요하므로, 목록용 AdminSessionRead와
    달리 의도적으로 노출한다."""

    session_prose: str | None


class AdminUserDetail(UserRead):
    """GET /admin/users/lookup 응답. 프로필 + 이 유저의 세션 전체(산문 포함)."""

    sessions: list[AdminSessionDetailRead]


class AdminProseUpdate(BaseModel):
    prose: str = Field(..., min_length=1)


class AdminEmailUpdate(BaseModel):
    new_email: EmailStr


class AdminPasswordReset(BaseModel):
    new_password: str = Field(
        ..., min_length=8, description="평문 비밀번호. Supabase Auth로만 전달되고 저장되지 않는다."
    )


class AdminDbTable(str, Enum):
    """DB 열람 화면이 조회를 허용하는 테이블 화이트리스트 — 경로 파라미터로 임의
    테이블/쿼리를 지정할 수 없도록 여기 나열된 것만 허용한다."""

    USERS = "users"
    SESSIONS = "sessions"
    EVENTS = "events"
    AUTOBIOGRAPHIES = "autobiographies"
    CHAPTER_DRAFTS = "chapter_drafts"


class AdminAuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admin_id: uuid.UUID
    action: str
    target_user_id: uuid.UUID | None
    target_session_id: uuid.UUID | None
    created_at: datetime


class AdminLogService(str, Enum):
    """애플리케이션 로그 열람 화면이 허용하는 서비스 화이트리스트 — 파일 경로
    조작을 막기 위해 임의 문자열이 아니라 이 목록만 허용한다."""

    BACKEND = "backend"
    WORKER = "worker"
    BEAT = "beat"


class AdminLogLinesRead(BaseModel):
    lines: list[str]
