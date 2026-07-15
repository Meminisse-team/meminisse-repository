import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AutobiographyStatus, DraftStatus


class AutobiographyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str | None
    status: AutobiographyStatus
    toc_data: dict | None
    style_bible: dict | None
    book_synopsis: str | None
    final_content: str | None
    pdf_url: str | None
    created_at: datetime
    updated_at: datetime


class TocCandidateSelect(BaseModel):
    candidate_index: int


class CustomizationOptionItem(BaseModel):
    """단일 선택지의 이름·설명·서술 예시."""
    key: str
    name: str
    description: str
    example: str | None = None


class CustomizationOptionsResponse(BaseModel):
    """사용 가능한 말투·구성·컨셉 선택지 전체 목록."""
    tones: list[CustomizationOptionItem]
    structures: list[CustomizationOptionItem]
    concepts: list[CustomizationOptionItem]


class CustomizationSelectionRequest(BaseModel):
    """각 카테고리에서 2개씩 선택."""
    tones: list[str]
    structures: list[str]
    concepts: list[str]


class CustomizationRecommendationResponse(BaseModel):
    """말투·구성·컨셉 추천 조합(하이브리드). 참고용 힌트일 뿐 강제가 아니며,
    tones/structures/concepts는 CustomizationSelectionRequest와 그대로 호환되는
    형태라 프론트가 select 폼의 기본값으로 바로 채울 수 있다.

    source: "content_based"(Phase 3 완료 후 — 실제 스타일 바이블·사건 내용을 LLM이
    읽고 판단) 또는 "tag_based"(Phase 3 이전 — 답변한 질문들의 사전 태그를 집계한
    즉석 힌트). reasoning은 content_based일 때만 LLM이 남긴 추천 근거가 채워진다."""
    tones: list[str]
    structures: list[str]
    concepts: list[str]
    source: str
    reasoning: str | None = None


class SamplePreviewItem(BaseModel):
    """8개 샘플 중 하나."""
    tone: str
    structure: str
    concept: str
    tone_name: str
    structure_name: str
    concept_name: str
    preview_text: str


class SamplePreviewsResponse(BaseModel):
    samples: list[SamplePreviewItem]


class CustomizationConfirmRequest(BaseModel):
    """최종 확정할 조합 1개."""
    tone: str
    structure: str
    concept: str


class ChapterDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    autobiography_id: uuid.UUID
    chapter_index: int
    title: str | None
    chapter_synopsis: str | None
    content: str | None
    source_event_ids: list[uuid.UUID]
    factcheck_report: dict | None
    groundedness_report: dict | None
    status: DraftStatus
    created_at: datetime
    updated_at: datetime
