import uuid

from fastapi import APIRouter

from app.api.deps import DbSession
from app.schemas.autobiography import AutobiographyRead
from app.services import autobiography_service

router = APIRouter(prefix="/autobiographies", tags=["autobiographies"])


@router.get("/{user_id}", response_model=AutobiographyRead)
async def get_autobiography(user_id: uuid.UUID, db: DbSession) -> AutobiographyRead:
    autobiography = await autobiography_service.get_or_create_autobiography(db, user_id)
    return AutobiographyRead.model_validate(autobiography)
