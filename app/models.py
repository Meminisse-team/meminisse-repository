import uuid
from enum import Enum as PyEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func

EMBEDDING_DIM = 1536  # OpenAI text-embedding-3-large


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserStage(str, PyEnum):
    ONBOARDING = "onboarding"
    INTERVIEW = "interview"    # 대화 진행 중
    PUBLISHING = "publishing"  # 목차 생성 및 챕터 조립 중
    PUBLISHED = "published"    # 자서전 출판 완료


class LifePeriod(str, PyEnum):
    """질문의 시간적 배경 분류. 사건 타임라인 정렬용 메타데이터. 챕터 구분 기준 아님."""
    CHILDHOOD = "childhood"
    YOUTH = "youth"
    ADULTHOOD = "adulthood"
    SENIOR = "senior"


class MediaAnalysisTrack(str, PyEnum):
    """Phase 1 듀얼 트랙 분류 결과."""
    TEXT_DOCUMENT = "text_document"  # 텍스트 포함 사진 → Upstage Document Parse 경로
    PURE_MEMORY = "pure_memory"      # 순수 추억 사진 → 유저 코멘트 경로


class SessionType(str, PyEnum):
    PHOTO = "photo"                    # 사진 핀셋 대화 (linked_media_asset_id 기반)
    FIXED_QUESTION = "fixed_question"  # 고정 템플릿 질문 (question_id 기반)


class SessionStatus(str, PyEnum):
    OPEN = "open"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class MessageRole(str, PyEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class AssetType(str, PyEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class DraftStatus(str, PyEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    FINALIZED = "finalized"


class AutobiographyStatus(str, PyEnum):
    IN_PROGRESS = "in_progress"    # 인터뷰 진행 중
    CONSOLIDATED = "consolidated"  # 모든 세션 완료 + 1인칭 산문 재조립 완료. '책 완성' 버튼 대기.
    PUBLISHED = "published"        # 최종 출판 완료


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

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
    autobiography = relationship(
        "Autobiography", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


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
        Enum(SessionType, name="sessiontype"),
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
        Enum(SessionStatus, name="sessionstatus"),
        nullable=False,
        default=SessionStatus.OPEN,
        server_default=SessionStatus.OPEN.value,
    )
    # 11개 슬롯(필수 5 + 추가 6) 충족 현황. 꼬리 질문 루프 발동 조건 판단에 사용.
    # {"place":true,"time":true,"event":false,"emotion":false,"values":false,
    #  "gratitude":false,"regret":false,"turning_point":false,
    #  "pride":false,"belief":false,"message":false}
    slots_filled = Column(JSONB, nullable=False, default=dict, server_default="{}")
    followup_count = Column(SmallInteger, nullable=False, default=0, server_default="0")  # 꼬리질문 횟수 추적 (max 2)
    is_must_include = Column(Boolean, nullable=False, default=False, server_default="false")  # '꼭 넣기' 체크
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="sessions")
    question = relationship(
        "Question",
        back_populates="sessions",
        foreign_keys="[InterviewSession.question_id]",
    )
    # 이 세션의 대화 소재가 된 사진 (PHOTO 세션 전용)
    linked_media_asset = relationship(
        "MediaAsset",
        primaryjoin="InterviewSession.linked_media_asset_id == MediaAsset.id",
        foreign_keys="[InterviewSession.linked_media_asset_id]",
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


class ChatLog(Base):
    """세션 내의 메시지 타래. extracted_labels와 embedding은 user 턴에만 적재."""
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
    role = Column(Enum(MessageRole, name="messagerole"), nullable=False)
    content = Column(Text, nullable=False)
    # user 턴 전용: Solar LLM이 추출한 11개 슬롯 값
    # {"place":"..","time":"..","event":"..","emotion":"..","values":"...",
    #  "gratitude":null,"regret":"..","turning_point":null,
    #  "pride":null,"belief":null,"message":null}
    extracted_labels = Column(JSONB, nullable=True)
    # user 턴 전용: 하이브리드 검색용 벡터 (OpenAI text-embedding-3-large, 1536차원)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)
    turn_index = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("InterviewSession", back_populates="chat_logs")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 어떤 인터뷰 세션 중에 업로드되었는지 (nullable: Phase 1 업로드는 세션 미존재)
    # FK 제약은 interview_sessions 생성 후 ALTER로 추가 (순환 참조 해소)
    session_id = Column(UUID(as_uuid=True), nullable=True)
    s3_key = Column(String(1024), unique=True, nullable=False)
    s3_url = Column(String(2048), nullable=False)
    asset_type = Column(
        Enum(AssetType, name="assettype"),
        nullable=False,
        default=AssetType.IMAGE,
        server_default=AssetType.IMAGE.value,
    )
    # Phase 1 업로드 메타데이터 (유저 선택 입력)
    age_at_time = Column(SmallInteger, nullable=True)       # 당시 나이 → 생애주기 큐 매핑에 사용
    location_at_time = Column(String(255), nullable=True)   # 당시 장소
    people_at_time = Column(Text, nullable=True)            # 당시 인물
    # Phase 1 듀얼 트랙 처리 결과
    life_period_mapped = Column(
        Enum(LifePeriod, name="lifeperiod"),
        nullable=True,
        comment="age_at_time 기반 매핑 결과. 생애주기 인터뷰 큐 우선순위 분류에 사용.",
    )
    analysis_track = Column(
        Enum(MediaAnalysisTrack, name="mediaanalysistrack"),
        nullable=True,
        comment="text_document=Upstage Document Parse 경로, pure_memory=유저 코멘트 경로",
    )
    pre_extracted_labels = Column(JSONB, nullable=True)  # Upstage Document Parse API 추출 라벨 (선(先) 라벨링)
    user_comment = Column(Text, nullable=True)           # 순수 추억 사진의 1차 유저 코멘트
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="media_assets")
    # 이 에셋이 업로드된 인터뷰 세션 (nullable)
    session = relationship(
        "InterviewSession",
        primaryjoin="MediaAsset.session_id == InterviewSession.id",
        foreign_keys="[MediaAsset.session_id]",
        back_populates="media_assets",
    )


class Autobiography(Base):
    """MVP: 유저 1인당 자서전 1권 (user_id UNIQUE 제약)."""
    __tablename__ = "autobiographies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    title = Column(String(512), nullable=True)
    status = Column(
        Enum(AutobiographyStatus, name="autobiographystatus"),
        nullable=False,
        default=AutobiographyStatus.IN_PROGRESS,
        server_default=AutobiographyStatus.IN_PROGRESS.value,
    )
    # Phase 3: 대화별 Raw 로그 → 1인칭 산문 재조립 결과 (필수/추가 라벨 및 원본 정보 누락 금지)
    # 모든 세션 완료 시 자동 생성. status=consolidated 진입 조건.
    consolidated_content = Column(Text, nullable=True)
    # Phase 4: 최종 렌더링 LLM이 생성한 목차 후보 3개 + 유저 선택 결과
    # {
    #   "generated_at": "...",
    #   "candidates": [
    #     {"index": 0, "chapters": [{"chapter_index": 1, "title": "...", "theme_keywords": [...]}]},
    #     {"index": 1, "chapters": [...]},
    #     {"index": 2, "chapters": [...]}
    #   ],
    #   "selected_candidate_index": null
    # }
    toc_data = Column(JSONB, nullable=True)
    final_content = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="autobiography")
    chapter_drafts = relationship(
        "ChapterDraft",
        back_populates="autobiography",
        order_by="ChapterDraft.chapter_index",
        cascade="all, delete-orphan",
    )


class ChapterDraft(Base):
    """챕터는 세션과 1:1이 아닌 M:N. 하이브리드 검색으로 수집된 여러 세션 기억을 조합하여 생성."""
    __tablename__ = "chapter_drafts"
    __table_args__ = (
        UniqueConstraint("autobiography_id", "chapter_index", name="uq_chapter_drafts_auto_idx"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    autobiography_id = Column(
        UUID(as_uuid=True),
        ForeignKey("autobiographies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_index = Column(Integer, nullable=False)
    title = Column(String(512), nullable=True)
    content = Column(Text, nullable=True)
    # 이 챕터 생성에 기여한 세션 ID 목록 (M:N, 단일 FK 불가)
    source_session_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}")
    status = Column(
        Enum(DraftStatus, name="draftstatus"),
        nullable=False,
        default=DraftStatus.DRAFT,
        server_default=DraftStatus.DRAFT.value,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    autobiography = relationship("Autobiography", back_populates="chapter_drafts")
