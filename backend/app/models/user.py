from sqlalchemy import Column, DateTime, ForeignKey, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base, str_enum
from app.models.enums import UserStage


class User(Base):
    """
    비밀번호를 비롯한 인증 관련 값은 이 테이블에 전혀 없다 — 회원가입/로그인은
    Supabase Auth(auth.users, GoTrue)가 전담한다(app/clients/supabase_auth.py).
    이 테이블은 그 auth.users 행 하나당 정확히 하나씩 존재하는 "프로필" 테이블이며,
    `id`는 새로 생성하지 않고 Supabase Auth가 발급한 auth.users.id를 그대로 쓴다
    (app/services/user_service.py:create_user 참조). alembic 004가 id 컬럼에
    auth.users(id) FK(ON DELETE CASCADE)를 걸어, Supabase 쪽에서 계정이 삭제되면
    이 프로필과 그에 딸린 모든 데이터(세션·이벤트·자서전 등)가 함께 정리된다.
    """

    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    birth_year = Column(SmallInteger, nullable=True)   # 당시 나이 기반 생애주기 매핑에 사용
    hometown = Column(String(255), nullable=True)      # 고향 (초기 프로필)
    current_stage = Column(
        str_enum(UserStage, name="userstage"),
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
    consent_records = relationship(
        "ConsentRecord", back_populates="user", cascade="all, delete-orphan"
    )
