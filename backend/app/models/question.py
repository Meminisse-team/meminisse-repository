import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.enums import LifePeriod


class Question(Base):
    __tablename__ = "questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sequence_order = Column(Integer, unique=True, nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    # 사건의 시간적 위치를 나타내는 메타데이터. 챕터 구분이 아닌 타임라인 정렬에만 사용.
    life_period = Column(
        Enum(LifePeriod, name="lifeperiod"),
        nullable=False,
        default=LifePeriod.CHILDHOOD,
        server_default=LifePeriod.CHILDHOOD.value,
    )
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sessions = relationship("InterviewSession", back_populates="question")
