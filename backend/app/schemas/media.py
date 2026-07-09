import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AssetType, LifePeriod, MediaAnalysisTrack


class MediaAssetCreate(BaseModel):
    user_id: uuid.UUID
    session_id: uuid.UUID | None = None
    asset_type: AssetType = AssetType.IMAGE
    age_at_time: int | None = None
    location_at_time: str | None = None
    people_at_time: str | None = None
    user_comment: str | None = None


class MediaAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID | None
    s3_url: str
    asset_type: AssetType
    age_at_time: int | None
    location_at_time: str | None
    people_at_time: str | None
    life_period_mapped: LifePeriod | None
    analysis_track: MediaAnalysisTrack | None
    user_comment: str | None
    created_at: datetime
