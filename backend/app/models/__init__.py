"""
SQLAlchemy 모델 패키지.

alembic/env.py의 `Base.metadata` 오토디스커버리 및 서비스 레이어의 임포트 편의를 위해
모든 모델과 enum을 여기서 재노출한다. 순환 참조 없이 상호 relationship()을 문자열로
연결하려면 mapper configure 이전에 모든 모델 클래스가 import되어 있어야 하므로,
이 파일이 그 시점을 보장하는 단일 진입점 역할을 한다.
"""

from app.models.admin import AdminAuditLog
from app.models.autobiography import Autobiography, ChapterDraft
from app.models.base import EMBEDDING_DIM, Base
from app.models.character import Character, CharacterMention
from app.models.consent import ConsentRecord
from app.models.enums import (
    AssetType,
    AutobiographyStatus,
    ConsentGrantedBy,
    ConsentType,
    DraftStatus,
    EducationLevel,
    EventRelationType,
    EventSourceType,
    LifeMilestoneCategory,
    LifePeriod,
    MaritalStatus,
    MediaAnalysisTrack,
    MessageRole,
    RiskClassification,
    SessionStatus,
    SessionType,
    UserRole,
    UserStage,
)
from app.models.event import Event, EventRelation
from app.models.interview import ChatLog, InterviewSession
from app.models.media import MediaAsset
from app.models.question import Question
from app.models.user import User

__all__ = [
    "Base",
    "EMBEDDING_DIM",
    "User",
    "UserStage",
    "UserRole",
    "EducationLevel",
    "MaritalStatus",
    "AdminAuditLog",
    "Question",
    "LifePeriod",
    "InterviewSession",
    "SessionType",
    "SessionStatus",
    "ChatLog",
    "MessageRole",
    "MediaAsset",
    "AssetType",
    "MediaAnalysisTrack",
    "Event",
    "EventRelation",
    "EventSourceType",
    "EventRelationType",
    "LifeMilestoneCategory",
    "Autobiography",
    "AutobiographyStatus",
    "ChapterDraft",
    "DraftStatus",
    "Character",
    "CharacterMention",
    "RiskClassification",
    "ConsentRecord",
    "ConsentType",
    "ConsentGrantedBy",
]
