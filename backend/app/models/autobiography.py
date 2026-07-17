import uuid

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base, str_enum
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
        str_enum(AutobiographyStatus, name="autobiographystatus"),
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
    # Phase 3 산출물: 화자 문체·상용 표현·가치관 키워드·전체 감정 아크를 담은 단일 문서.
    # 이후 모든 집필 프롬프트에 전역 상수로 주입되어 어조·생애 철학의 일관성을 보장한다.
    style_bible = Column(JSONB, nullable=True)
    # Phase 4 1단계 산출물: 책 전체 시놉시스. 챕터 순차 생성이 아닌 하향식 집필의 설계도.
    book_synopsis = Column(Text, nullable=True)
    final_content = Column(Text, nullable=True)
    # Phase 5: Jinja2+WeasyPrint로 조판한 국판(A5) PDF의 S3 URL. final_content가
    # 채워진 뒤에만 생성 가능하다(app/services/pdf_service.py 참조). POD(주문형 인쇄)
    # 발주 연계는 범위 밖 — 이 필드는 완성된 PDF 파일 위치까지만 담당한다.
    pdf_url = Column(String(2048), nullable=True)
    # 사용자가 PDF 조판 직전에 직접 고른 수록 사진 배치(2026-07-16). 기획안 5절의
    # 고정 슬롯 템플릿 원칙에 따라 [{media_asset_id, chapter_index, slot, caption}]
    # 배열로만 표현한다(slot: "chapter_top" | "full_page_before"). NULL(미지정)과
    # 빈 배열 모두 조판 시 "사진 없음" — pdf_service는 여기 지정된 사진만 넣는다.
    photo_placements = Column(JSONB, nullable=True)
    # 최종 자서전 확정 시점에 설정. 이 날짜 이후 Layer 0 원문 로그(chat_logs 등)는
    # 사용자 옵트인이 없는 한 자동 삭제 대상이 된다(개인정보보호법상 최소보유 원칙, 기획안 5절).
    raw_log_retention_until = Column(Date, nullable=True)
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
    characters = relationship(
        "Character", back_populates="autobiography", cascade="all, delete-orphan"
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
    # Phase 4 2단계 산출물: 이 챕터의 시놉시스. 책 전체 시놉시스 아래 하향식으로 생성된다.
    chapter_synopsis = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    # 이 챕터 집필에 소환된 Event ID 목록 (M:N, 단일 FK 불가). RAG 검색 결과 + 팩트체크 시
    # 대조 대상이 되는 원천 이벤트 레코드를 그대로 추적한다.
    source_event_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}")
    # 원문 대조 팩트체크(재추출-정규화-대조) 결과. 라벨 값과 불일치하는 팩트만 플래그되어
    # 최종 검토 화면에 표시된다. 예: {"flags": [{"claim": "...", "expected": "...", "found": "..."}]}
    factcheck_report = Column(JSONB, nullable=True)
    # 근거 검증(Groundedness Check) 결과. 문장-출처 이벤트 문단 쌍의 NLI 함의 판정.
    # 예: {"flags": [{"sentence": "...", "reason": "not_entailed_by_sources"}]}
    groundedness_report = Column(JSONB, nullable=True)
    status = Column(
        str_enum(DraftStatus, name="draftstatus"),
        nullable=False,
        default=DraftStatus.DRAFT,
        server_default=DraftStatus.DRAFT.value,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    autobiography = relationship("Autobiography", back_populates="chapter_drafts")
