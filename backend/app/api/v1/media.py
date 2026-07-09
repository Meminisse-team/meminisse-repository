import uuid

from fastapi import APIRouter, Form, UploadFile, status

from app.api.deps import DbSession
from app.models import AssetType
from app.schemas.media import MediaAssetCreate, MediaAssetRead
from app.services import media_service

router = APIRouter(prefix="/media-assets", tags=["media"])


@router.post("", response_model=MediaAssetRead, status_code=status.HTTP_201_CREATED)
async def upload_media_asset(
    db: DbSession,
    file: UploadFile,
    user_id: uuid.UUID = Form(...),
    session_id: uuid.UUID | None = Form(None),
    asset_type: AssetType = Form(AssetType.IMAGE),
    age_at_time: int | None = Form(None),
    location_at_time: str | None = Form(None),
    people_at_time: str | None = Form(None),
    user_comment: str | None = Form(None),
) -> MediaAssetRead:
    payload = MediaAssetCreate(
        user_id=user_id,
        session_id=session_id,
        asset_type=asset_type,
        age_at_time=age_at_time,
        location_at_time=location_at_time,
        people_at_time=people_at_time,
        user_comment=user_comment,
    )
    file_bytes = await file.read()
    asset = await media_service.upload_media_asset(
        db,
        payload,
        file_bytes=file_bytes,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
    )
    return MediaAssetRead.model_validate(asset)
