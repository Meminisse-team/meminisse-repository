import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base, str_enum
from app.models.enums import RiskClassification


class Character(Base):
    """
    최종 원고에 등장하는 구술자 본인 외 제3자(기획안 Phase 4 '등장인물 검토', 6절 법적 리스크 관리).

    NER 스캔 + 이벤트 추출 시 확보된 인물 라벨 교차 대조로 채워지며, real_name_retained는
    항상 false를 기본값으로 한다(전수 가명화 opt-out 정책). risk_classification은 가명 적용
    여부를 결정하는 게이트가 아니라, 실명 유지 시도 시 고지 강도만 조정하는 보조 신호이므로
    분류 오류가 발생해도 가명 기본값이라는 안전 상태는 변하지 않는다.
    """
    __tablename__ = "characters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    autobiography_id = Column(
        UUID(as_uuid=True),
        ForeignKey("autobiographies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_name = Column(String(100), nullable=False)  # 원고에 노출되는 이름 (기본값: 가명)
    real_name = Column(String(100), nullable=True)       # NER 스캔으로 확보된 실명. 구술자 확인/추가 지정 가능.
    relation_to_user = Column(String(100), nullable=True)  # 예: "어머니의 친구", "첫째 형"
    risk_classification = Column(
        str_enum(RiskClassification, name="riskclassification"),
        nullable=False,
        default=RiskClassification.NONE,
        server_default=RiskClassification.NONE.value,
    )
    # 전수 가명화 기본값(opt-out). true로 전환은 인물 단위 법적 책임 고지문 확인·동의 후에만 허용.
    real_name_retained = Column(Boolean, nullable=False, default=False, server_default="false")
    disclosure_notice_version = Column(String(50), nullable=True)  # 확인한 고지문 버전(주의의무 이행 증빙)
    disclosure_acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    autobiography = relationship("Autobiography", back_populates="characters")
    mentions = relationship(
        "CharacterMention", back_populates="character", cascade="all, delete-orphan"
    )


class CharacterMention(Base):
    """
    등장인물이 실제로 언급되는 위치(이벤트 문단 또는 챕터 본문) 추적. 서술 성격 분류(범죄·비위
    언급, 부정적 인물 평가, 갈등 당사자 여부)의 근거가 되는 문맥을 참조할 수 있게 한다.

    이벤트 단계(Phase 2 후처리 직후)와 챕터 단계(Phase 4 최종 검토) 양쪽에서 생성될 수 있으므로
    event_id/chapter_draft_id 중 최소 하나는 채워지는 것을 전제로 한다(DB 레벨 CHECK 제약은
    두 FK 모두 nullable해야 하는 SQLAlchemy 제약상 애플리케이션 레이어에서 검증).
    """
    __tablename__ = "character_mentions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id = Column(
        UUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id = Column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chapter_draft_id = Column(
        UUID(as_uuid=True), ForeignKey("chapter_drafts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    character = relationship("Character", back_populates="mentions")
    event = relationship("Event", foreign_keys=[event_id])
    chapter_draft = relationship("ChapterDraft", foreign_keys=[chapter_draft_id])
