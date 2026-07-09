import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AutobiographyStatus


class AutobiographyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str | None
    status: AutobiographyStatus
    toc_data: dict | None
    created_at: datetime
    updated_at: datetime
