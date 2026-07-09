import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Numeric, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import EMBEDDING_DIM, Base
from app.models.enums import EventRelationType, EventSourceType, LifeMilestoneCategory, LifePeriod


class Event(Base):
    """
    이벤트 1급 객체화(기획안 원칙 1)의 핵심 테이블이자 Layer 1(검증 계층)의 실체.

    하나의 답변/문서에 여러 사건이 섞여 있어도(예: "A라는 시련이 있었고 B로 극복했다")
    각 사건을 독립 레코드로 분리 저장한다. 한 레코드는 [라벨 + 요약 + 대응 산문 문단 +
    임베딩 + 출처]로 구성되며, 세션 산문을 통째로 청킹하지 않으므로 RAG 검색 정확도와
    목차 생성용 중요도 신호 조회를 같은 테이블에서 즉시 처리할 수 있다.

    verified=false인 레코드(OCR 오인식 의심 등)는 embedding이 null로 유지되며 RAG/집필
    파이프라인에서 완전히 제외된다. 해당 생애주기 인터뷰 시점에 확인 질문으로 제시되어
    사용자가 확인한 후에만 verified=true로 승격된다.
    """
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    source_type = Column(Enum(EventSourceType, name="eventsourcetype"), nullable=False)
    # source_type == SESSION_CHAT 전용. DOCUMENT면 null.
    session_id = Column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=True
    )
    # source_type == DOCUMENT 전용 (Document Parse 결과 기반). SESSION_CHAT이면 null.
    media_asset_id = Column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    # 원문 대조 재생성을 위한 근거 포인터.
    # SESSION_CHAT 예: {"chat_log_id": "...", "char_start": 120, "char_end": 260}
    # DOCUMENT 예:     {"element_id": 4, "page": 2}  (Document Parse elements[].id)
    source_span = Column(JSONB, nullable=True)

    life_period = Column(Enum(LifePeriod, name="lifeperiod"), nullable=True)
    # 시기가 불확실한 사건을 위한 상대적/범위형 표현 ("고등학교 시절", "1980년대 초").
    occurred_at_label = Column(String(100), nullable=True)
    place = Column(String(255), nullable=True)
    people = Column(Text, nullable=True)
    one_line_summary = Column(Text, nullable=False)
    # Layer 2 산문 중 이 사건에 대응하는 문단. 그 자체가 RAG 검색 소스.
    prose_paragraph = Column(Text, nullable=False)

    emotion_tag = Column(String(50), nullable=True)
    emotion_intensity = Column(SmallInteger, nullable=True)  # 1~5
    # 명시 발화 없이 정황상 추론된 감정 태그인 경우 true.
    # 최종 집필 시 단정적 감정 서술의 근거로 사용하지 않는다(기획안 Phase 2 후처리 항목).
    emotion_inferred = Column(Boolean, nullable=False, default=False, server_default="false")

    # 나머지 슬롯(가치관/감사/후회/전환점/자부심/신념/메시지 등) 원본 값. 미확정 슬롯은 null 허용.
    labels = Column(JSONB, nullable=False, default=dict, server_default="{}")
    # 슬롯별 confidence 플래그. 억지 채움으로 인한 할루시네이션 방지용.
    confidence = Column(JSONB, nullable=True)

    # Layer 1 검증 게이트. false인 동안 embedding은 null이며 RAG/집필에서 제외된다.
    verified = Column(Boolean, nullable=False, default=False, server_default="false")
    is_must_include = Column(Boolean, nullable=False, default=False, server_default="false")

    # Phase 3 중요도 스코어링(기획안: "계산 가능한 신호의 가중합", 사용자 내 z-score 정규화 적용).
    # 목차 후보 생성 시 정렬 키로 사용. precision=9: '꼭 넣기' 고정 가산점(1000)이 다른 신호를
    # 압도해야 하므로(서비스 레이어 MUST_INCLUDE_BONUS) Numeric(6,3)로는 자릿수가 부족하다.
    importance_score = Column(Numeric(9, 3), nullable=True)
    # 스코어 산출 근거 스냅샷 — "왜 이 사건이 목차에 들어갔는가"를 재현 가능하게 설명하기 위함.
    # 예: {"raw_length": 420, "emotion_intensity": 4, "mention_count": 2, "z_score": 1.8}
    importance_signals = Column(JSONB, nullable=True)
    life_milestone_category = Column(
        Enum(LifeMilestoneCategory, name="lifemilestonecategory"), nullable=True
    )

    # verified=true로 승격된 이후에만 채워짐 (Upstage embedding-passage).
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)

    # Phase 3 중복 이벤트 병합 결과. 병합되어 흡수된 이벤트는 병합 대상 id를 남기고
    # 조회/RAG에서 제외된다. 판정 불확실 시 병합하지 않는 것이 기본값(과병합 리스크 회피).
    duplicate_of_event_id = Column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="events")
    session = relationship(
        "InterviewSession",
        foreign_keys=[session_id],
        back_populates="events",
    )
    media_asset = relationship(
        "MediaAsset",
        foreign_keys=[media_asset_id],
        back_populates="events",
    )
    duplicate_of = relationship("Event", remote_side=[id])


class EventRelation(Base):
    """사건 간 관계(원인·극복 등). 기획안 원칙 1: 각 사건이 '사건 간 관계'를 갖는다."""
    __tablename__ = "event_relations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_event_id = Column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_event_id = Column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type = Column(Enum(EventRelationType, name="eventrelationtype"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    from_event = relationship("Event", foreign_keys=[from_event_id])
    to_event = relationship("Event", foreign_keys=[to_event_id])
