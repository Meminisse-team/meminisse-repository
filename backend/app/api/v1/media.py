import uuid

from fastapi import APIRouter, Form, UploadFile, status

from app.api.deps import CurrentUserDep, GatewaysDep
from app.models import AssetType
from app.schemas.media import MediaAssetCreate, MediaAssetRead
from app.services import media_service

router = APIRouter(prefix="/media-assets", tags=["media"])


@router.post("", response_model=MediaAssetRead, status_code=status.HTTP_201_CREATED)
async def upload_media_asset(
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
    file: UploadFile,
    session_id: uuid.UUID | None = Form(None),
    asset_type: AssetType = Form(AssetType.IMAGE),
    age_at_time: int | None = Form(None),
    location_at_time: str | None = Form(None),
    people_at_time: str | None = Form(None),
    user_comment: str | None = Form(None),
) -> MediaAssetRead:
    """user_id는 더 이상 Form 필드로 받지 않는다 — 인증 토큰의 current_user.id를
    그대로 쓴다(다른 사람 명의로 업로드하는 경로를 차단하기 위함). session_id를
    지정한 경우 그 세션이 본인 소유인지는 media_service가 아니라 세션 자체를
    다루는 인터뷰 라우터의 책임 범위이므로 여기서는 검증하지 않는다 — 향후
    세션-소유자 교차검증이 필요하면 이 지점에 추가할 것(TODO)."""
    payload = MediaAssetCreate(
        user_id=current_user.id,
        session_id=session_id,
        asset_type=asset_type,
        age_at_time=age_at_time,
        location_at_time=location_at_time,
        people_at_time=people_at_time,
        user_comment=user_comment,
    )
    file_bytes = await file.read()
    asset = await media_service.upload_media_asset(
        gateways,
        payload,
        file_bytes=file_bytes,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
    )
    return MediaAssetRead.model_validate(asset)
