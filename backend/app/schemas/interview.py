import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import MessageRole, SessionStatus, SessionType


class SessionCreate(BaseModel):
    user_id: uuid.UUID
    session_type: SessionType
    question_id: uuid.UUID | None = None
    linked_media_asset_id: uuid.UUID | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    session_type: SessionType
    question_id: uuid.UUID | None
    linked_media_asset_id: uuid.UUID | None
    status: SessionStatus
    slots_filled: dict[str, bool]
    followup_count: int
    is_must_include: bool
    started_at: datetime
    completed_at: datetime | None


class ChatMessageCreate(BaseModel):
    content: str


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: MessageRole
    content: str
    turn_index: int
    created_at: datetime


class TurnResponse(BaseModel):
    """유저 턴 전송 후 응답: 저장된 유저/에이전트 메시지 + 세션 상태."""

    user_message: ChatMessageRead
    assistant_message: ChatMessageRead
    session: SessionRead
