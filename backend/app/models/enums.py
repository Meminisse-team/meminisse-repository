from enum import Enum as PyEnum


class UserStage(str, PyEnum):
    ONBOARDING = "onboarding"
    INTERVIEW = "interview"    # 대화 진행 중
    PUBLISHING = "publishing"  # 목차 생성 및 챕터 조립 중
    PUBLISHED = "published"    # 자서전 출판 완료


class LifePeriod(str, PyEnum):
    """질문/사건의 시간적 배경 분류. 타임라인 정렬용 메타데이터. 챕터 구분 기준 아님."""
    CHILDHOOD = "childhood"
    YOUTH = "youth"
    ADULTHOOD = "adulthood"
    SENIOR = "senior"


class MediaAnalysisTrack(str, PyEnum):
    """Phase 1 듀얼 트랙 분류 결과."""
    TEXT_DOCUMENT = "text_document"  # 텍스트 포함 사진 → Upstage Document Parse 경로
    PURE_MEMORY = "pure_memory"      # 순수 추억 사진 → 유저 코멘트 경로


class SessionType(str, PyEnum):
    PHOTO = "photo"                    # 사진 핀셋 대화 (linked_media_asset_id 기반)
    FIXED_QUESTION = "fixed_question"  # 고정 템플릿 질문 (question_id 기반)


class SessionStatus(str, PyEnum):
    OPEN = "open"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class MessageRole(str, PyEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class AssetType(str, PyEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class DraftStatus(str, PyEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    FINALIZED = "finalized"


class AutobiographyStatus(str, PyEnum):
    IN_PROGRESS = "in_progress"    # 인터뷰 진행 중
    CONSOLIDATED = "consolidated"  # 모든 세션 완료 + 이벤트 추출 완료. '책 완성' 버튼 대기.
    PUBLISHED = "published"        # 최종 출판 완료


class EventSourceType(str, PyEnum):
    """이벤트(Event)가 어느 경로에서 추출되었는지. 원칙 1(이벤트 1급 객체화)의 출처 분기."""
    SESSION_CHAT = "session_chat"  # 인터뷰 대화 산문에서 Solar가 분할·추출
    DOCUMENT = "document"          # Document Parse OCR 결과에서 추출 (Layer 1 검증 게이트 대상)


class EventRelationType(str, PyEnum):
    """사건 간 관계. 기획안 원칙 1: '사건 간 관계(원인·극복 등)를 가진 레코드'."""
    CAUSE = "cause"              # from_event가 to_event의 원인
    OVERCOME = "overcome"        # from_event가 to_event를 극복함
    FOLLOWED_BY = "followed_by"  # 시간상 연속
    RELATED = "related"          # 기타 연관
