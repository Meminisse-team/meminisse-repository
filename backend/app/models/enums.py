from enum import Enum as PyEnum


class UserStage(str, PyEnum):
    ONBOARDING = "onboarding"
    INTERVIEW = "interview"    # 대화 진행 중
    PUBLISHING = "publishing"  # 목차 생성 및 챕터 조립 중
    PUBLISHED = "published"    # 자서전 출판 완료


class UserRole(str, PyEnum):
    """권한 레벨. 관리자 대시보드(파이프라인 상태·위기 대응 로그 조회) 접근 게이트로만
    쓰인다 — 일반 서비스 로직은 role을 참조하지 않는다."""
    USER = "user"
    ADMIN = "admin"


class EducationLevel(str, PyEnum):
    """가입 시 라디오 버튼으로 직접 입력받는 최종 학력(선택 응답, 2026-07-16 설계
    변경 — 대화 내용 추론 대신 명시적 입력으로 확정). 질문 필터링(app/data/
    question_bank.py의 eligibility)이 참조한다."""
    ELEMENTARY = "elementary"
    MIDDLE_SCHOOL = "middle_school"
    HIGH_SCHOOL = "high_school"
    UNIVERSITY = "university"
    GRADUATE_SCHOOL = "graduate_school"


class MaritalStatus(str, PyEnum):
    """가입 시 라디오 버튼으로 직접 입력받는 혼인 여부(선택 응답). EducationLevel과
    동일한 이유로 질문 필터링이 참조한다."""
    SINGLE = "single"
    MARRIED = "married"
    DIVORCED = "divorced"
    WIDOWED = "widowed"


class LifePeriod(str, PyEnum):
    """질문/사건의 시간적 배경 분류. 타임라인 정렬용 메타데이터. 챕터 구분 기준 아님."""
    CHILDHOOD = "childhood"
    YOUTH = "youth"
    ADULTHOOD = "adulthood"
    SENIOR = "senior"


class MediaAnalysisTrack(str, PyEnum):
    """Phase 1 듀얼 트랙 분류 결과. 캡션(image_caption)은 두 트랙 모두에서 항상
    생성된다 — 이 값은 순전히 "Azure Vision이 사진 속에서 읽어낸 텍스트가
    있었는가"만 가른다(app/services/media_service.py)."""
    TEXT_DOCUMENT = "text_document"  # 사진 속 텍스트(메모 등) 검출됨 → image_ocr_text 채워짐
    PURE_MEMORY = "pure_memory"      # 텍스트 없음 → 캡션(image_caption)에만 의존


class SessionType(str, PyEnum):
    PHOTO = "photo"                    # 사진 핀셋 대화 (linked_media_asset_id 기반)
    FIXED_QUESTION = "fixed_question"  # 고정 템플릿 질문 (question_id 기반)
    EPISODE = "episode"                # 자유 에피소드 — question_id/linked_media_asset_id 둘 다
                                        # null, 자동 배정 큐를 거치지 않고 사용자가 직접 시작


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
    DOCUMENT = "document"          # 사진 속 텍스트(Azure Vision) 결과에서 추출 (Layer 1 검증 게이트 대상)


class EventRelationType(str, PyEnum):
    """사건 간 관계. 기획안 원칙 1: '사건 간 관계(원인·극복 등)를 가진 레코드'."""
    CAUSE = "cause"              # from_event가 to_event의 원인
    OVERCOME = "overcome"        # from_event가 to_event를 극복함
    FOLLOWED_BY = "followed_by"  # 시간상 연속
    RELATED = "related"          # 기타 연관


class LifeMilestoneCategory(str, PyEnum):
    """중요도 스코어링의 생애 이정표 카테고리 매칭 신호(기획안 Phase 3, 회상요법 문헌 기반 범주)."""
    MARRIAGE = "marriage"
    CHILDBIRTH = "childbirth"
    CAREER_CHANGE = "career_change"
    ILLNESS = "illness"
    BEREAVEMENT = "bereavement"
    RELOCATION = "relocation"
    RETIREMENT = "retirement"
    OTHER = "other"


class RiskClassification(str, PyEnum):
    """등장인물 서술 성격 분류(기획안 Phase 4/6절). 가명 적용 여부의 게이트가 아니라
    실명 유지 시도 시 고지 강도만 조정하는 보조 신호."""
    NONE = "none"
    NEGATIVE_PORTRAYAL = "negative_portrayal"
    CONFLICT = "conflict"
    CRIME_MENTION = "crime_mention"


class ConsentType(str, PyEnum):
    """동의 기록 종류(기획안 5절 동의 주체 분리, 6절 실명 유지 고지)."""
    DATA_COLLECTION = "data_collection"        # 온보딩 첫 세션: 정보주체 본인의 데이터 수집·이용 동의
    DISCLOSURE_REALNAME = "disclosure_realname"  # 인물 단위 실명 유지 법적 책임 고지 동의
    RETENTION_EXTENSION = "retention_extension"  # 원문 로그 보관 기간 연장 옵트인


class ConsentGrantedBy(str, PyEnum):
    """동의 행위자. 자녀가 온보딩을 세팅하더라도 동의 자체는 정보주체 본인에게 받아야 한다."""
    SELF = "self"          # 정보주체(부모) 본인
    GUARDIAN = "guardian"  # 보호자/자녀 대리
