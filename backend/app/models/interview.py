import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base, str_enum
from app.models.enums import MessageRole, SessionStatus, SessionType


class InterviewSession(Base):
    """
    하나의 메인 질문(fixed_question) 또는 사진(photo)에 대해 열리는 대화 단위.

    session_type == PHOTO        → linked_media_asset_id 필수, question_id null
    session_type == FIXED_QUESTION → question_id 필수, linked_media_asset_id null
    """
    __tablename__ = "interview_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_type = Column(
        str_enum(SessionType, name="sessiontype"),
        nullable=False,
        default=SessionType.FIXED_QUESTION,
        server_default=SessionType.FIXED_QUESTION.value,
    )
    # FIXED_QUESTION 세션 전용. PHOTO 세션이면 null.
    question_id = Column(UUID(as_uuid=True), ForeignKey("questions.id"), nullable=True)
    # PHOTO 세션 전용. 이 대화가 기반하는 원본 사진. FIXED_QUESTION 세션이면 null.
    linked_media_asset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = Column(
        str_enum(SessionStatus, name="sessionstatus"),
        nullable=False,
        default=SessionStatus.OPEN,
        server_default=SessionStatus.OPEN.value,
    )
    # 12개 슬롯(필수 6 + 추가 6) 충족 현황. 꼬리 질문 루프 발동 조건 판단(경량 게이팅)에만 사용하고
    # 세션 종료 후 정밀 추출(Event 테이블) 단계에서는 참조하지 않는다.
    # {"place":true,"time":true,"event":false,"emotion":false,"values":false,
    #  "gratitude":false,"regret":false,"turning_point":false,
    #  "pride":false,"belief":false,"message":false}
    slots_filled = Column(JSONB, nullable=False, default=dict, server_default="{}")
    followup_count = Column(SmallInteger, nullable=False, default=0, server_default="0")  # 꼬리질문 횟수 추적 (max 2)
    is_must_include = Column(Boolean, nullable=False, default=False, server_default="false")  # '꼭 넣기' 체크
    # Phase 2 후처리: 세션 로그 원문(Layer 0)을 보수적으로 재조립한 1인칭 산문 (Layer 2).
    # 문장 병합·재배열 없이 어미/추임새만 정돈. NLI 왜곡 탐지의 대조 대상이며,
    # 이후 Solar가 이 산문을 사건 단위로 분할해 Event.prose_paragraph로 쪼갠다.
    session_prose = Column(Text, nullable=True)
    # OCR 확인 질문(prompts.build_ocr_confirmation_question)을 이번 턴에 냈다면 그
    # 대상 Event. 다음 유저 발화는 슬롯 게이팅이 아니라 이 확인에 대한 답으로 해석된다
    # (interview_service.add_user_turn 참조). 응답 처리 후 다시 null로 되돌아간다.
    pending_ocr_confirmation_event_id = Column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="sessions")
    question = relationship(
        "Question",
        back_populates="sessions",
        foreign_keys=[question_id],
    )
    # 이 세션의 대화 소재가 된 사진 (PHOTO 세션 전용)
    linked_media_asset = relationship(
        "MediaAsset",
        primaryjoin="InterviewSession.linked_media_asset_id == MediaAsset.id",
        foreign_keys=[linked_media_asset_id],
        uselist=False,
    )
    chat_logs = relationship(
        "ChatLog", back_populates="session", order_by="ChatLog.turn_index", cascade="all, delete-orphan"
    )
    # 이 세션 중 업로드된 미디어 에셋
    media_assets = relationship(
        "MediaAsset",
        primaryjoin="InterviewSession.id == MediaAsset.session_id",
        foreign_keys="[MediaAsset.session_id]",
        back_populates="session",
    )
    events = relationship(
        "Event",
        primaryjoin="InterviewSession.id == Event.session_id",
        foreign_keys="[Event.session_id]",
        back_populates="session",
    )


class ChatLog(Base):
    """
    세션 내의 메시지 타래. Layer 0(불변 원천) 그 자체이므로 라벨/임베딩을 들고 있지 않는다.

    대화 중 슬롯 게이팅 결과(다음 질문 여부 판단용)는 저비용 판별로 즉시 계산·소비되고
    영속화하지 않는다(기획안: "이 결과는 다음 질문 게이팅에만 사용하고 폐기한다").
    정밀 라벨 추출 결과는 세션 종료 후 Event 테이블에 사건 단위로 저장된다.
    """
    __tablename__ = "chat_logs"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="uq_chat_logs_session_turn"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(str_enum(MessageRole, name="messagerole"), nullable=False)
    content = Column(Text, nullable=False)
    turn_index = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("InterviewSession", back_populates="chat_logs")
