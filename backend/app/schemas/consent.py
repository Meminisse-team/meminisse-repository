import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ConsentGrantedBy, ConsentType


class ConsentCreate(BaseModel):
    consent_type: ConsentType
    notice_version: str
    granted_by: ConsentGrantedBy


class ConsentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    consent_type: ConsentType
    notice_version: str
    granted_by: ConsentGrantedBy
    granted_at: datetime
    revoked_at: datetime | None
