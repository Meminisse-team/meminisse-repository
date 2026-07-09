"""
게이트웨이 계층의 데이터 계약(Data Transfer Object)을 정의한다.

기획안의 4계층 데이터 모델(Layer 0~3)을 그대로 반영하되, SQLAlchemy나 boto3 같은
특정 구현 기술에 의존하지 않는 순수 dataclass로 표현한다. 서비스 레이어는 이 DTO만
알면 되고, 실제로 Postgres에서 왔는지 인메모리 Mock에서 왔는지는 몰라도 된다 —
이것이 "DB 객체만 갈아 끼울 수 있도록" 하는 경계선이다.

app/schemas/*.py(Pydantic, API 요청/응답 계약)와 의도적으로 분리했다. API 계약이
바뀐다고(예: 페이지네이션 필드 추가) 게이트웨이 계약까지 흔들리면 안 되기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.models.enums import (
    AssetType,
    AutobiographyStatus,
    EventRelationType,
    EventSourceType,
    LifePeriod,
    MediaAnalysisTrack,
    MessageRole,
    SessionStatus,
    SessionType,
    UserStage,
)

# --------------------------------------------------------------------------- #
# User                                                                         #
# --------------------------------------------------------------------------- #


@dataclass
class UserRecord:
    id: UUID
    email: str
    name: str
    birth_year: int | None
    hometown: str | None
    current_stage: UserStage


@dataclass
class UserCreateData:
    email: str
    name: str
    birth_year: int | None = None
    hometown: str | None = None


# --------------------------------------------------------------------------- #
# Layer 0 (불변 원천) — 대화 로그 원문 / InterviewSession                       #
# Layer 2 (세션 산문) — InterviewSessionRecord.session_prose                   #
# --------------------------------------------------------------------------- #


@dataclass
class ChatLogRecord:
    id: UUID
    session_id: UUID
    role: MessageRole
    content: str
    turn_index: int
    created_at: datetime


@dataclass
class InterviewSessionRecord:
    id: UUID
    user_id: UUID
    session_type: SessionType
    question_id: UUID | None
    linked_media_asset_id: UUID | None
    status: SessionStatus
    slots_filled: dict[str, bool]
    followup_count: int
    is_must_include: bool
    session_prose: str | None
    started_at: datetime
    completed_at: datetime | None
    chat_logs: list[ChatLogRecord] = field(default_factory=list)


@dataclass
class SessionCreateData:
    user_id: UUID
    session_type: SessionType
    question_id: UUID | None = None
    linked_media_asset_id: UUID | None = None
    initial_slots_filled: dict[str, bool] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Layer 1 (검증 계층) — Event / EventRelation                                  #
# --------------------------------------------------------------------------- #


@dataclass
class EventRecord:
    id: UUID
    user_id: UUID
    source_type: EventSourceType
    session_id: UUID | None
    media_asset_id: UUID | None
    source_span: dict | None
    life_period: LifePeriod | None
    occurred_at_label: str | None
    place: str | None
    people: str | None
    one_line_summary: str
    prose_paragraph: str
    emotion_tag: str | None
    emotion_intensity: int | None
    emotion_inferred: bool
    labels: dict
    confidence: dict | None
    verified: bool
    is_must_include: bool
    embedding: list[float] | None
    created_at: datetime


@dataclass
class EventCreateData:
    """
    verified/embedding을 호출부가 직접 지정한다 — SESSION_CHAT 경로(왜곡 탐지 통과 시
    즉시 verified=True)와 DOCUMENT 경로(OCR 확인 전까지 verified=False, embedding=None)의
    승격 시점이 다르기 때문에(app/services/event_extraction_service.py,
    app/services/media_service.py 참조), 리포지토리가 임의로 기본값을 정하지 않는다.
    """

    user_id: UUID
    source_type: EventSourceType
    one_line_summary: str
    prose_paragraph: str
    verified: bool
    session_id: UUID | None = None
    media_asset_id: UUID | None = None
    source_span: dict | None = None
    life_period: LifePeriod | None = None
    occurred_at_label: str | None = None
    place: str | None = None
    people: str | None = None
    emotion_tag: str | None = None
    emotion_intensity: int | None = None
    emotion_inferred: bool = False
    labels: dict = field(default_factory=dict)
    confidence: dict | None = None
    embedding: list[float] | None = None


@dataclass
class EventRelationCreateData:
    from_event_id: UUID
    to_event_id: UUID
    relation_type: EventRelationType


# --------------------------------------------------------------------------- #
# Layer 0 — 미디어 원본 메타데이터 (실제 바이트는 ObjectStorageRepository가 별도 관리) #
# --------------------------------------------------------------------------- #


@dataclass
class MediaAssetRecord:
    id: UUID
    user_id: UUID
    session_id: UUID | None
    s3_key: str
    s3_url: str
    asset_type: AssetType
    age_at_time: int | None
    location_at_time: str | None
    people_at_time: str | None
    life_period_mapped: LifePeriod | None
    analysis_track: MediaAnalysisTrack | None
    pre_extracted_labels: dict | None
    user_comment: str | None
    created_at: datetime


@dataclass
class MediaAssetCreateData:
    user_id: UUID
    s3_key: str
    s3_url: str
    asset_type: AssetType
    session_id: UUID | None = None
    age_at_time: int | None = None
    location_at_time: str | None = None
    people_at_time: str | None = None
    life_period_mapped: LifePeriod | None = None
    user_comment: str | None = None


# --------------------------------------------------------------------------- #
# Layer 3 (최종 원고) — Autobiography                                          #
# --------------------------------------------------------------------------- #


@dataclass
class AutobiographyRecord:
    id: UUID
    user_id: UUID
    title: str | None
    status: AutobiographyStatus
    toc_data: dict | None
    created_at: datetime
    updated_at: datetime
