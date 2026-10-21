"""Common contract for new quantitative reports; historical v1 stays immutable."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class RecallScore(BaseModel):
    recall: float | None = Field(ge=0, le=1)
    survived: int = Field(ge=0)
    total: int = Field(ge=0)
    missing_keywords: list[str] = []

class EvaluationMetadata(BaseModel):
    created_at: str
    git_revision: str
    python_version: str
    source_sha256: str
    manuscript_sha256: str
    keyword_fixture_sha256: str
    keyword_fixture: dict
    normalizer_version: str
    cutoffs: list[int]
    judge_model: str
    judge_reasoning_effort: str
    geval_requested_runs: int = Field(ge=1)
    geval_completed_runs: int = Field(ge=0)
    chapter_concurrency: int = Field(ge=1)
    regenerate: bool

class QuantitativeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2"]
    evaluation: EvaluationMetadata
    email: str
    title: str | None
    final_content_length: int = Field(ge=0)
    phase2_distortion_detection: dict
    phase4_own_verification: dict
    information_preservation_recall: dict[str, RecallScore]
    narrative_coherence_geval: dict
    structure: dict
