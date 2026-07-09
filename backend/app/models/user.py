import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.enums import UserStage


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    birth_year = Column(SmallInteger, nullable=True)   # 당시 나이 기반 생애주기 매핑에 사용
    hometown = Column(String(255), nullable=True)      # 고향 (초기 프로필)
    current_stage = Column(
        Enum(UserStage, name="userstage"),
        nullable=False,
        default=UserStage.ONBOARDING,
        server_default=UserStage.ONBOARDING.value,
    )
    # 정수 인덱스 대신 FK를 사용하여 질문 비활성화 시 인덱스 깨짐 방지.
    current_question_id = Column(
        UUID(as_uuid=True),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    current_question = relationship("Question", foreign_keys=[current_question_id])
    sessions = relationship("InterviewSession", back_populates="user", cascade="all, delete-orphan")
    media_assets = relationship("MediaAsset", back_populates="user", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="user", cascade="all, delete-orphan")
    autobiography = relationship(
        "Autobiography", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
