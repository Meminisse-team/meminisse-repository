import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.enums import AssetType, LifePeriod, MediaAnalysisTrack


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
    # Document Parse 원시 응답 캐시(디버깅/감사용 raw staging). 검증 게이트를 통과한 실제
    # 사건 데이터는 이 필드가 아니라 Event(source_type=DOCUMENT, verified=true)에 저장된다.
    pre_extracted_labels = Column(JSONB, nullable=True)
    user_comment = Column(Text, nullable=True)           # 순수 추억 사진의 1차 유저 코멘트
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="media_assets")
    # 이 에셋이 업로드된 인터뷰 세션 (nullable)
    session = relationship(
        "InterviewSession",
        primaryjoin="MediaAsset.session_id == InterviewSession.id",
        foreign_keys=[session_id],
        back_populates="media_assets",
    )
    events = relationship(
        "Event",
        primaryjoin="MediaAsset.id == Event.media_asset_id",
        foreign_keys="[Event.media_asset_id]",
        back_populates="media_asset",
    )
