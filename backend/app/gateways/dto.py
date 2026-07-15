"""
게이트웨이 계층의 데이터 계약(Data Transfer Object)을 정의한다.

기획안의 4계층 데이터 모델(Layer 0~3)을 그대로 반영하되, SQLAlchemy나 boto3 같은
특정 구현 기술에 의존하지 않는 순수 dataclass로 표현한다. 서비스 레이어는 이 DTO만
알면 되고, 실제로 Postgres에서 왔는지 인메모리 Mock에서 왔는지는 몰라도 된다 —
이것이 "DB 객체만 갈아 끼울 수 있도록" 하는 경계선이다.

app/schemas/*.py(Pydantic, API 요청/응답 계약)와 의도적으로 분리했다. API 계약이
바뀐다고(예: 페이지네이션 필드 추가) 게이트웨이 계약까지 흔들리면 안 되기 때문이다.

`ConsentGrant`라는 이름을 쓴 이유: ORM 모델이 이미 `app.models.ConsentRecord`라는
이름을 쓰고 있어(다른 팀원의 Phase3/4 작업), 같은 이름의 DTO를 또 만들면 "이게 ORM
객체인지 DTO인지" 코드만 보고 헷갈리게 된다. 그래서 DTO 쪽은 의미가 같은
`ConsentGrant`로 다르게 지었다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.models.enums import (
    AssetType,
    AutobiographyStatus,
    ConsentGrantedBy,
    ConsentType,
    DraftStatus,
    EventRelationType,
    EventSourceType,
    LifeMilestoneCategory,
    LifePeriod,
    MediaAnalysisTrack,
    MessageRole,
    RiskClassification,
    SessionStatus,
    SessionType,
    UserRole,
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
    role: UserRole = UserRole.USER


@dataclass
class UserCreateData:
    """`id`는 이 프로젝트가 새로 생성하지 않는다 — Supabase Auth Admin API가
    회원가입 시점에 `auth.users`를 먼저 만들고 발급한 id를 그대로 받아 쓴다
    (app/services/user_service.py:create_user, app/clients/supabase_auth.py 참조).
    비밀번호 관련 필드가 없는 이유도 동일 — 비밀번호는 Supabase Auth만 알고 있다."""

    id: UUID
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
    session_prose_original: str | None = None
    prose_last_edited_at: datetime | None = None


@dataclass
class SessionCreateData:
    user_id: UUID
    session_type: SessionType
    question_id: UUID | None = None
    linked_media_asset_id: UUID | None = None
    initial_slots_filled: dict[str, bool] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 관리자 감사 로그 (app/models/admin.py:AdminAuditLog)                          #
# --------------------------------------------------------------------------- #


@dataclass
class AdminAuditLogRecord:
    id: UUID
    admin_id: UUID
    action: str
    target_user_id: UUID | None
    target_session_id: UUID | None
    created_at: datetime


@dataclass
class AdminAuditLogCreateData:
    admin_id: UUID
    action: str
    target_user_id: UUID | None = None
    target_session_id: UUID | None = None


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
    # Phase 3 이벤트 병합·중요도 산정 결과 (팀원의 Phase3/4 작업분과 통합하며 추가).
    duplicate_of_event_id: UUID | None = None
    importance_score: Decimal | None = None
    importance_signals: dict | None = None
    life_milestone_category: LifeMilestoneCategory | None = None


@dataclass
class EventCreateData:
    """
    verified/embedding을 호출부가 직접 지정한다 — SESSION_CHAT 경로(왜곡 탐지 통과 시
    즉시 verified=True)와 DOCUMENT 경로(OCR 확인 전까지 verified=False, embedding=None)의
    승격 시점이 다르기 때문에(app/services/event_extraction_service.py,
    app/services/media_service.py 참조), 게이트웨이가 임의로 기본값을 정하지 않는다.
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


@dataclass
class EventImportanceUpdate:
    """EventGateway.bulk_update_importance()의 입력 단위."""

    event_id: UUID
    importance_score: Decimal
    importance_signals: dict
    life_milestone_category: LifeMilestoneCategory | None


# --------------------------------------------------------------------------- #
# Layer 0 — 미디어 원본 메타데이터 (실제 바이트는 ObjectStorageGateway가 별도 관리) #
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
    image_caption: str | None
    image_ocr_text: str | None
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
# Layer 3 (최종 원고) — Autobiography / ChapterDraft                           #
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
    # Phase 3/4 (팀원 작업분과 통합하며 추가).
    consolidated_content: str | None = None
    style_bible: dict | None = None
    book_synopsis: str | None = None
    final_content: str | None = None
    pdf_url: str | None = None


@dataclass
class ChapterDraftRecord:
    id: UUID
    autobiography_id: UUID
    chapter_index: int
    title: str | None
    chapter_synopsis: str | None
    content: str | None
    source_event_ids: list[UUID]
    factcheck_report: dict | None
    groundedness_report: dict | None
    status: DraftStatus
    created_at: datetime
    updated_at: datetime


@dataclass
class ChapterDraftCreateData:
    chapter_index: int
    title: str | None = None


@dataclass
class ChapterDraftWriteResult:
    """ChapterDraftGateway.save_write_result()의 입력 — write_chapter() 파이프라인 산출물 일괄 반영."""

    source_event_ids: list[UUID]
    chapter_synopsis: str
    content: str
    factcheck_report: dict
    groundedness_report: dict
    status: DraftStatus


# --------------------------------------------------------------------------- #
# 등장인물 검토 (기획안 Phase 4 / 6절 법적 리스크 관리)                          #
# --------------------------------------------------------------------------- #


@dataclass
class CharacterRecord:
    id: UUID
    autobiography_id: UUID
    display_name: str
    real_name: str | None
    relation_to_user: str | None
    risk_classification: RiskClassification
    real_name_retained: bool
    disclosure_notice_version: str | None
    disclosure_acknowledged_at: datetime | None
    created_at: datetime


@dataclass
class CharacterCreateData:
    """display_name은 넣지 않는다 — 게이트웨이가 relation_to_user 또는 자동 채번
    ("지인 N")으로 항상 스스로 정한다(원본 서비스 로직 그대로 유지)."""

    autobiography_id: UUID
    real_name: str
    relation_to_user: str | None = None


# --------------------------------------------------------------------------- #
# 동의 기록 (기획안 5절 동의 주체 분리, 6절 주의의무 이행 증빙)                   #
# --------------------------------------------------------------------------- #


@dataclass
class ConsentGrant:
    id: UUID
    user_id: UUID
    consent_type: ConsentType
    notice_version: str
    granted_by: ConsentGrantedBy
    granted_at: datetime
    revoked_at: datetime | None
    character_id: UUID | None = None


@dataclass
class ConsentGrantCreateData:
    user_id: UUID
    consent_type: ConsentType
    notice_version: str
    granted_by: ConsentGrantedBy
    character_id: UUID | None = None


# --------------------------------------------------------------------------- #
# 고정 인터뷰 질문 큐 (app/data/question_bank.py 시드 데이터의 조회 계약)          #
# --------------------------------------------------------------------------- #


@dataclass
class QuestionRecord:
    id: UUID
    sequence_order: int
    title: str
    content: str
    life_period: LifePeriod
    is_active: bool
