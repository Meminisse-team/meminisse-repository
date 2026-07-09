from fastapi import APIRouter

from app.api.v1 import autobiographies, interviews, media, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(users.router)
api_router.include_router(interviews.router)
api_router.include_router(media.router)
api_router.include_router(autobiographies.router)
