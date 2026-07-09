import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.enums import AutobiographyStatus, DraftStatus


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
    # 모든 세션의 InterviewSession.session_prose를 생애주기 순으로 이어붙인 열람용 원본.
    # LLM 입력으로 재사용하지 않는다 — Phase 4 집필은 Event 레코드 기반 하이브리드 RAG를
    # 사용하므로(4절 참조), 이 필드는 '책 완성' 대기 화면에서 유저가 원본을 훑어보는
    # 용도에 한정된다. 모든 세션 완료 시 자동 생성되며 status=CONSOLIDATED 진입 조건.
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
    """
    챕터는 세션과 1:1이 아닌 M:N. 하향식 집필(책 시놉시스 → 챕터 시놉시스 → 본문) 단계에서
    하이브리드 검색(의미 검색 + 키워드 정확 매칭)으로 소환된 여러 Event 레코드를 조합하여 생성된다.
    """
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
    # 이 챕터 집필에 소환된 Event ID 목록 (M:N, 단일 FK 불가). RAG 검색 결과 + 팩트체크 시
    # 대조 대상이 되는 원천 이벤트 레코드를 그대로 추적한다.
    source_event_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}")
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
