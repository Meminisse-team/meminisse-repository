import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import EventSourceType, LifeMilestoneCategory, LifePeriod


class EventRead(BaseModel):
    """GET /events(나의 이야기 탭) 응답 단위. Event 모델 전체를 그대로 노출하지
    않는다 — embedding(4096차원 벡터, 클라이언트가 쓸 일이 없음)과 source_span/
    labels/confidence(내부 디버깅·감사용 필드)는 의도적으로 뺐다. 이 스키마가
    반환되는 시점의 레코드는 항상 verified=true·미병합 상태다(EventGateway.
    list_for_timeline의 게이트 — app/gateways/interfaces.py 참조)이므로
    verified/duplicate_of_event_id 필드도 노출하지 않는다(값이 항상 고정이라
    의미가 없음)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: EventSourceType
    session_id: uuid.UUID | None
    media_asset_id: uuid.UUID | None
    life_period: LifePeriod | None
    occurred_at_label: str | None
    place: str | None
    people: str | None
    one_line_summary: str
    prose_paragraph: str
    emotion_tag: str | None
    emotion_intensity: int | None
    emotion_inferred: bool
    is_must_include: bool
    life_milestone_category: LifeMilestoneCategory | None
    created_at: datetime
