"""
'나의 이야기' 탭 — 세션 단위 카드 조회 전용 라우터. app/api/v1/events.py(사건 단위
조회)와 관심사가 달라 별도 파일로 뒀다 — story_service.py 모듈 docstring 참조.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep, GatewaysDep
from app.schemas.story import StoryCardRead
from app.services import story_service

router = APIRouter(prefix="/stories", tags=["stories"])


@router.get("", response_model=list[StoryCardRead])
async def list_stories(gateways: GatewaysDep, current_user: CurrentUserDep) -> list[StoryCardRead]:
    cards = await story_service.list_story_cards(gateways, current_user.id)
    return [StoryCardRead.model_validate(card) for card in cards]
