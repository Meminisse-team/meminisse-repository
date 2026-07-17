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
    # True면 세션은 끝났지만(status=COMPLETED) Celery 산문 재조립이 아직 안 끝나
    # prose가 빈 문자열인 placeholder 카드다 — 프론트가 "생성 중..." 임시 셀로
    # 표시한다(story_service.py:list_story_cards 참조).
    is_generating: bool = False


class StoryCardPageRead(BaseModel):
    """GET /stories 응답 봉투 — items는 이 페이지 분량(limit/offset), total은
    이 유저의 완료된 세션 전체 개수(프론트 페이지 번호 UI의 총 페이지 수
    계산용). app/services/story_service.py:StoryCardPage 참조."""

    items: list[StoryCardRead]
    total: int


class StoryProseUpdate(BaseModel):
    """PATCH /stories/{session_id}. 사용자가 재조립된 산문을 직접 고쳐 저장할 때."""

    prose: str = Field(..., min_length=1)
