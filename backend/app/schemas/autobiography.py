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
