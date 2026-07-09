import uuid

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.enums import UserStage


class UserCreate(BaseModel):
    email: EmailStr
    name: str
    birth_year: int | None = None
    hometown: str | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    name: str
    birth_year: int | None
    hometown: str | None
    current_stage: UserStage
