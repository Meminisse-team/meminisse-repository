import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import MessageRole, SessionStatus, SessionType


class SessionCreate(BaseModel):
    """user_id는 요청 바디에 넣지 않는다 — 인증 토큰(현재 로그인한 사용자)에서 항상
    유도한다(app/api/v1/interviews.py). 클라이언트가 임의의 user_id를 실어 보내
    남의 세션을 만들 수 있는 경로를 원천 차단하기 위함."""

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


class SessionDetailRead(SessionRead):
    """GET /interview-sessions/{id} 전용 — SessionRead에 chat_logs를 더한 것.
    목록 조회(GET /interview-sessions)는 여러 세션을 한 번에 내려주므로 가벼운
    SessionRead를 쓰고, 단건 조회는 대화를 이어보거나 되짚어볼 수 있도록 전체
    발화를 함께 반환한다(turn_index 오름차순 — InterviewSessionGateway.get_by_id
    계약 참조)."""

    chat_logs: list[ChatMessageRead]


class TurnResponse(BaseModel):
    """유저 턴 전송 후 응답: 저장된 유저/에이전트 메시지 + 세션 상태."""

    user_message: ChatMessageRead
    assistant_message: ChatMessageRead
    session: SessionRead
