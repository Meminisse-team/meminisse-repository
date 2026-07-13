import uuid

from fastapi import APIRouter, Form, HTTPException, UploadFile, status

from app.api.deps import CurrentUserDep, GatewaysDep
from app.models import AssetType
from app.schemas.media import MediaAssetCreate, MediaAssetRead
from app.services import interview_service, media_service

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
    지정한 경우 그 세션이 본인 소유인지 여기서 검증한다 — 검증 없이는 다른 사용자의
    session_id를 넣어 그 인터뷰 세션에 사진을 연결시킬 수 있었다(교차 테넌트 데이터
    오염)."""
    if session_id is not None:
        session = await interview_service.get_session(gateways, session_id)
        if session is None or session.user_id != current_user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다.")

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


@router.get("", response_model=list[MediaAssetRead])
async def list_media_assets(gateways: GatewaysDep, current_user: CurrentUserDep) -> list[MediaAssetRead]:
    """본인이 업로드한 미디어 전체를 최근 업로드순으로 반환한다(사진첩 탭)."""
    assets = await media_service.list_media_assets(gateways, current_user.id)
    return [MediaAssetRead.model_validate(asset) for asset in assets]


@router.get("/{media_asset_id}", response_model=MediaAssetRead)
async def get_media_asset(
    gateways: GatewaysDep, current_user: CurrentUserDep, media_asset_id: uuid.UUID
) -> MediaAssetRead:
    """PHOTO 세션 채팅 화면이 linked_media_asset_id로 사진 원본을 조회할 때 쓴다."""
    asset = await media_service.get_media_asset(gateways, media_asset_id)
    if asset is None or asset.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "미디어를 찾을 수 없습니다.")
    return MediaAssetRead.model_validate(asset)
