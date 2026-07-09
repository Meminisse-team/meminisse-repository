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
    created_at: datetime
    updated_at: datetime


class TocCandidateSelect(BaseModel):
    candidate_index: int


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
