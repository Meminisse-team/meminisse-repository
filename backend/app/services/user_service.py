import uuid

from app.gateways.dto import UserCreateData, UserRecord
from app.gateways.factory import Gateways
from app.schemas.user import UserCreate


async def create_user(gateways: Gateways, payload: UserCreate) -> UserRecord:
    user = await gateways.users.create(
        UserCreateData(
            email=payload.email,
            name=payload.name,
            birth_year=payload.birth_year,
            hometown=payload.hometown,
        )
    )
    await gateways.commit()
    return user


async def get_user(gateways: Gateways, user_id: uuid.UUID) -> UserRecord | None:
    return await gateways.users.get_by_id(user_id)


async def get_user_by_email(gateways: Gateways, email: str) -> UserRecord | None:
    return await gateways.users.get_by_email(email)
