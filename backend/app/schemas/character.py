import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import RiskClassification


class CharacterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    autobiography_id: uuid.UUID
    display_name: str
    real_name: str | None
    relation_to_user: str | None
    risk_classification: RiskClassification
    real_name_retained: bool
    disclosure_notice_version: str | None
    disclosure_acknowledged_at: datetime | None
    created_at: datetime


class RetainRealNameRequest(BaseModel):
    notice_version: str
