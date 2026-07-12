import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ConsentGrantedBy, ConsentType


class ConsentCreate(BaseModel):
    consent_type: ConsentType
    notice_version: str
    granted_by: ConsentGrantedBy
    # DISCLOSURE_REALNAME(인물 단위 실명 유지 동의)일 때만 채운다 — 그 외 동의
    # 종류(DATA_COLLECTION 등)는 사용자 단위이므로 비워 둔다.
    character_id: uuid.UUID | None = None


class ConsentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    consent_type: ConsentType
    notice_version: str
    granted_by: ConsentGrantedBy
    granted_at: datetime
    revoked_at: datetime | None
    character_id: uuid.UUID | None
