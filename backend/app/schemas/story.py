import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StoryCardRead(BaseModel):
    """GET /stories(나의 이야기 탭) 응답 단위 — 사건이 아니라 세션 단위 카드.
    app/services/story_service.py:StoryCard 참조."""

    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    title: str
    subtitle: str | None
    prose: str
    completed_at: datetime | None


class StoryProseUpdate(BaseModel):
    """PATCH /stories/{session_id}. 사용자가 재조립된 산문을 직접 고쳐 저장할 때."""

    prose: str = Field(..., min_length=1)
