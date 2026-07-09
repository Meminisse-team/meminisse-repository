import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import GatewaysDep
from app.schemas.consent import ConsentCreate, ConsentRead
from app.schemas.user import UserCreate, UserRead
from app.services import consent_service, user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, gateways: GatewaysDep) -> UserRead:
    existing = await user_service.get_user_by_email(gateways, payload.email)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 등록된 이메일입니다.")
    user = await user_service.create_user(gateways, payload)
    return UserRead.model_validate(user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: uuid.UUID, gateways: GatewaysDep) -> UserRead:
    user = await user_service.get_user(gateways, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "사용자를 찾을 수 없습니다.")
    return UserRead.model_validate(user)


@router.post("/{user_id}/consents", response_model=ConsentRead, status_code=status.HTTP_201_CREATED)
async def create_consent(user_id: uuid.UUID, payload: ConsentCreate, gateways: GatewaysDep) -> ConsentRead:
    """
    정보주체 동의 기록(기획안 5절). 자녀가 온보딩을 대신 세팅하더라도 데이터 수집·
    이용 동의는 정보주체(부모) 본인에게 직접 받아야 하므로 granted_by로 행위자를
    구분해 남긴다.
    """
    user = await user_service.get_user(gateways, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "사용자를 찾을 수 없습니다.")
    record = await consent_service.record_consent(
        gateways,
        user_id,
        consent_type=payload.consent_type,
        notice_version=payload.notice_version,
        granted_by=payload.granted_by,
    )
    return ConsentRead.model_validate(record)


@router.get("/{user_id}/consents", response_model=list[ConsentRead])
async def get_consents(user_id: uuid.UUID, gateways: GatewaysDep) -> list[ConsentRead]:
    records = await consent_service.list_consents(gateways, user_id)
    return [ConsentRead.model_validate(record) for record in records]
