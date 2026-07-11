from fastapi import APIRouter

from app.api.v1 import auth, autobiographies, events, interviews, media, sandbox, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(users.router)
api_router.include_router(auth.router)
api_router.include_router(interviews.router)
api_router.include_router(media.router)
api_router.include_router(events.router)
api_router.include_router(autobiographies.router)
api_router.include_router(sandbox.router)
