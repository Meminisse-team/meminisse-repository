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
    # Azure Vision 캡션(사진의 시각적 내용을 설명하는 한 문장)과 사진 속에서 읽어낸
    # 텍스트. 둘 다 분석이 아직 안 끝났거나(Celery 대기 중) Azure 미설정이면 null.
    image_caption: str | None
    image_ocr_text: str | None
    user_comment: str | None
    created_at: datetime
