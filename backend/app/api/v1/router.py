from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    autobiographies,
    interviews,
    legal,
    media,
    sandbox,
    stories,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(users.router)
api_router.include_router(auth.router)
api_router.include_router(interviews.router)
api_router.include_router(media.router)
api_router.include_router(stories.router)
api_router.include_router(autobiographies.router)
api_router.include_router(legal.router)
api_router.include_router(sandbox.router)
api_router.include_router(admin.router)
