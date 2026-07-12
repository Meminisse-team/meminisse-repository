"""
게이트웨이 인터페이스 (추상 기반 클래스).

"Gateway"는 PoEAA/DDD에서 외부 자원(DB, 오브젝트 스토리지 등)에 대한 접근을 캡슐화하는
객체를 가리키는 표준 용어다. 이 프로젝트에서는 Git 저장소 이름(meminisse-repository)과
혼동되지 않도록 "Repository" 대신 이 용어를 사용한다.

서비스 레이어는 오직 이 인터페이스에만 의존한다. 팀원이 완성된 Postgres/pgvector,
S3 연동 코드를 가져오면, 이 인터페이스를 상속받는 새 구현체 하나만 만들어
app/gateways/factory.py의 조립 지점에 연결하면 된다 — 서비스/라우터 코드는
한 줄도 바꿀 필요가 없다.

`EventGateway.search_verified`는 Layer 1 검증 게이트를 강제하는 핵심 메서드다.
어떤 구현체든 verified=False 이거나 embedding이 없는 레코드를 반환하면 안 된다 —
이 계약을 어기면 검증되지 않은 데이터가 RAG에 유입되는, 기획안이 명시적으로
차단하고자 한 오염 경로가 그대로 뚫리게 된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.gateways.dto import (
    AutobiographyRecord,
    ChapterDraftCreateData,
    ChapterDraftRecord,
    ChapterDraftWriteResult,
    CharacterCreateData,
    CharacterRecord,
    ChatLogRecord,
    ConsentGrant,
    ConsentGrantCreateData,
    EventCreateData,
    EventImportanceUpdate,
    EventRecord,
    EventRelationCreateData,
    InterviewSessionRecord,
    MediaAssetCreateData,
    MediaAssetRecord,
    QuestionRecord,
    SessionCreateData,
    UserCreateData,
    UserRecord,
)
from app.models.enums import (
    AutobiographyStatus,
    ConsentType,
    MediaAnalysisTrack,
    MessageRole,
    RiskClassification,
    UserStage,
)


class ObjectStorageGateway(ABC):
    """Layer 0(불변 원천) 원본 파일 저장소. 실제 구현은 S3, Mock 구현은 인메모리 dict."""

    @abstractmethod
    async def put_object(self, key: str, data: bytes, *, content_type: str) -> str:
        """객체를 저장하고 접근 가능한 URL을 반환한다."""

    @abstractmethod
    async def get_presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        """제한 시간 동안 유효한 접근 URL을 발급한다."""

    @abstractmethod
    async def get_object(self, key: str) -> bytes:
        """저장된 객체의 원본 바이트를 그대로 읽어온다. Celery 워커가 업로드 요청 때
        받았던 파일을 다시 필요로 할 때 쓴다(예: 사진 분석을 비동기로 미룰 때) —
        큰 바이트 페이로드를 브로커(Redis) 메시지에 그대로 실어 보내지 않기 위함."""


class UserGateway(ABC):
    @abstractmethod
    async def create(self, data: UserCreateData) -> UserRecord: ...

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> UserRecord | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> UserRecord | None: ...

    @abstractmethod
    async def update(
        self,
        user_id: UUID,
        *,
        name: str | None = None,
        birth_year: int | None = None,
        hometown: str | None = None,
        current_stage: UserStage | None = None,
    ) -> UserRecord:
        """None인 인자는 "건드리지 않는다"는 뜻이다(부분 갱신). 소셜 로그인 온보딩
        완성 단계(PATCH /users/{id})와 일반 프로필 수정, 그리고 서비스 레이어가
        진행 단계 전환 시점(첫 인터뷰 세션 생성/Phase 3 완료/최종 윤문 완료)에
        current_stage만 갱신할 때도 함께 쓰인다."""


class InterviewSessionGateway(ABC):
    """
    InterviewSession을 애그리게이트 루트로 보고 ChatLog(자식 엔티티)를 함께 다룬다.
    세션 없이 채팅 로그만 단독으로 조회/생성할 일이 없으므로 별도 게이트웨이로
    분리하지 않았다 — 항상 세션 컨텍스트 안에서만 의미가 있다.
    """

    @abstractmethod
    async def create(self, data: SessionCreateData) -> InterviewSessionRecord: ...

    @abstractmethod
    async def get_by_id(self, session_id: UUID) -> InterviewSessionRecord | None:
        """chat_logs를 turn_index 순으로 포함해 반환한다."""

    @abstractmethod
    async def add_chat_log(
        self, session_id: UUID, *, role: MessageRole, content: str
    ) -> ChatLogRecord:
        """turn_index는 게이트웨이가 자동 채번한다."""

    @abstractmethod
    async def update_slots(
        self, session_id: UUID, *, slots_filled: dict[str, bool], followup_count: int
    ) -> None: ...

    @abstractmethod
    async def set_session_prose(self, session_id: UUID, prose: str) -> None:
        """Layer 2: 세션 종료 후 재조립된 1인칭 산문을 기록한다."""

    @abstractmethod
    async def set_pending_ocr_confirmation(
        self, session_id: UUID, event_id: UUID | None
    ) -> None:
        """이번 턴에 OCR 확인 질문을 냈다면 그 대상 event_id를, 응답을 처리하고 나면
        None을 넘겨 초기화한다."""

    @abstractmethod
    async def complete(self, session_id: UUID) -> None:
        """status=COMPLETED, completed_at=now로 전이한다."""

    @abstractmethod
    async def list_session_prose_by_user(self, user_id: UUID) -> list[str]:
        """session_prose가 채워진 완료 세션들을 started_at 오름차순으로. Phase 3
        consolidated_content 조립 및 스타일 바이블 생성 입력으로 쓰인다."""

    @abstractmethod
    async def list_by_user(self, user_id: UUID) -> list[InterviewSessionRecord]:
        """이 유저의 세션 전체를 started_at 내림차순(최신 순)으로 반환한다
        (GET /interview-sessions, 대시보드 '오늘의 대화'가 이어갈 세션을 찾는 데 사용).
        chat_logs는 채우지 않는다 — 목록 조회에서 매 세션의 전체 대화를 함께
        내려주면 페이로드가 불필요하게 커진다(전체 대화는 get_by_id로 개별 조회)."""


class EventGateway(ABC):
    """Layer 1(검증 계층)의 실체. Event/EventRelation을 다룬다."""

    @abstractmethod
    async def create(self, data: EventCreateData) -> EventRecord: ...

    @abstractmethod
    async def bulk_create(self, data: Sequence[EventCreateData]) -> list[EventRecord]:
        """세션 종료 후 한 번에 여러 사건이 추출되는 경로(이벤트 분할)에서 사용."""

    @abstractmethod
    async def bulk_update_embeddings(
        self, updates: Sequence[tuple[UUID, list[float]]]
    ) -> None:
        """(event_id, embedding) 쌍 목록으로 생성 이후 계산된 임베딩을 일괄 반영한다."""

    @abstractmethod
    async def create_relations(self, relations: Sequence[EventRelationCreateData]) -> None: ...

    @abstractmethod
    async def search_verified(
        self, *, user_id: UUID, query_embedding: list[float], limit: int = 10
    ) -> list[EventRecord]:
        """
        RAG용 시맨틱 검색. 반드시 verified=True AND embedding IS NOT NULL AND
        duplicate_of_event_id IS NULL(Phase 3 병합으로 흡수되지 않은 이벤트)인 레코드만
        반환해야 한다(Layer 1 검증 게이트). 이 조건은 호출부가 아니라 구현체 내부에서
        강제되어야 하며, 어떤 인자를 넘기더라도 우회할 수 없어야 한다.
        """

    @abstractmethod
    async def search_by_keywords(
        self, *, user_id: UUID, keywords: Sequence[str], limit: int = 10
    ) -> list[EventRecord]:
        """
        Phase 4 하이브리드 검색의 키워드 정확 매칭 축. search_verified와 동일하게
        verified=True AND duplicate_of_event_id IS NULL만 대상으로 한다.
        """

    @abstractmethod
    async def list_by_ids(self, event_ids: Sequence[UUID]) -> list[EventRecord]:
        """주어진 id 목록을 importance_score 내림차순(null은 마지막)으로 반환한다."""

    @abstractmethod
    async def list_unmerged_verified(self, user_id: UUID) -> list[EventRecord]:
        """verified=True AND duplicate_of_event_id IS NULL. embedding 유무는 따지지 않는다
        (Phase 3 중요도 산정·목차 생성 대상 조회용 — RAG 게이트인 search_verified와는 용도가 다르다).
        importance_score 내림차순(null은 마지막)으로 정렬해 반환한다."""

    @abstractmethod
    async def list_for_timeline(self, user_id: UUID) -> list[EventRecord]:
        """list_unmerged_verified와 필터 조건은 동일(verified=True AND
        duplicate_of_event_id IS NULL)하지만 정렬 기준이 다르다 — 이쪽은
        created_at 내림차순(최근에 나눈 대화가 먼저)으로, '나의 이야기' 탭처럼
        사용자가 시간순으로 훑어보는 화면 전용이다(GET /events). 목차 생성용
        중요도 정렬(list_unmerged_verified)과 표시용 시간 정렬의 관심사가 달라
        메서드를 분리했다 — 같은 쿼리에 정렬 파라미터를 얹지 않은 이유."""

    @abstractmethod
    async def list_mergeable(self, user_id: UUID) -> list[EventRecord]:
        """Phase 3 병합 후보 순회 대상: verified=True, 아직 흡수되지 않음, embedding 존재.
        created_at 오름차순(먼저 등장한 이벤트를 canonical으로 우선 채택)."""

    @abstractmethod
    async def find_merge_candidates(
        self,
        *,
        user_id: UUID,
        exclude_event_id: UUID,
        embedding: list[float],
        max_distance: float,
        limit: int,
    ) -> list[EventRecord]:
        """자기 자신을 제외하고, 코사인 거리가 max_distance보다 가까운 미병합 이벤트만
        거리 오름차순으로 반환한다. 실제 병합 여부는 서비스 레이어의 LLM 쌍별 판정이 결정한다."""

    @abstractmethod
    async def mark_duplicate(self, event_id: UUID, *, duplicate_of_event_id: UUID) -> None: ...

    @abstractmethod
    async def count_mentions(self, event_ids: Sequence[UUID]) -> dict[UUID, int]:
        """event_ids 각각을 duplicate_of_event_id로 가리키는(=흡수된) 이벤트 수. 반복 언급 신호."""

    @abstractmethod
    async def bulk_update_importance(self, updates: Sequence[EventImportanceUpdate]) -> None: ...

    @abstractmethod
    async def list_pending_document_confirmation(self, user_id: UUID) -> list[EventRecord]:
        """source_type=DOCUMENT AND verified=False인 이벤트(OCR 1차 검증에서 오인식
        의심으로 격리된 것들). 인터뷰 턴에서 확인 질문으로 제시할 대상 큐 —
        created_at 오름차순(먼저 스테이징된 것부터 확인)."""

    @abstractmethod
    async def set_verified(self, event_id: UUID, *, verified: bool) -> EventRecord:
        """OCR 확인 질문에 대한 유저 응답 결과를 반영한다. 승격(verified=True)
        이후의 임베딩 계산은 이 메서드의 책임이 아니다 — 호출부가 bulk_update_
        embeddings로 별도 반영한다."""

    @abstractmethod
    async def delete(self, event_id: UUID) -> None:
        """OCR 확인 질문에서 유저가 부인한(오인식이었다고 답한) 이벤트를 폐기한다."""


class MediaAssetGateway(ABC):
    @abstractmethod
    async def create(self, data: MediaAssetCreateData) -> MediaAssetRecord: ...

    @abstractmethod
    async def get_by_id(self, media_asset_id: UUID) -> MediaAssetRecord | None:
        """비동기 사진 분석 태스크(analyze_media_asset)가 워커에서 원본을 다시
        조회할 때 쓴다."""

    @abstractmethod
    async def update_analysis(
        self,
        media_asset_id: UUID,
        *,
        analysis_track: MediaAnalysisTrack,
        pre_extracted_labels: dict | None,
    ) -> None:
        """Phase 1 듀얼 트랙 분석 결과(Document Parse 산출물)를 기록한다."""

    @abstractmethod
    async def list_by_user(self, user_id: UUID) -> list[MediaAssetRecord]:
        """이 유저가 업로드한 미디어 전체를 created_at 내림차순(최근 업로드가
        먼저)으로 반환한다(GET /media-assets, 사진첩 탭)."""


class AutobiographyGateway(ABC):
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> AutobiographyRecord | None: ...

    @abstractmethod
    async def get_by_id(self, autobiography_id: UUID) -> AutobiographyRecord | None: ...

    @abstractmethod
    async def create(self, user_id: UUID) -> AutobiographyRecord: ...

    @abstractmethod
    async def update(
        self,
        autobiography_id: UUID,
        *,
        title: str | None = None,
        status: AutobiographyStatus | None = None,
        consolidated_content: str | None = None,
        style_bible: dict | None = None,
        toc_data: dict | None = None,
        book_synopsis: str | None = None,
        final_content: str | None = None,
        pdf_url: str | None = None,
    ) -> AutobiographyRecord:
        """None인 인자는 "이 필드는 건드리지 않는다"는 뜻이다 — 부분 갱신 전용이며,
        이 도메인에서는 위 필드들을 의도적으로 null로 되돌리는 경우가 없으므로
        None을 미지정 센티널로 사용해도 안전하다."""


class ChapterDraftGateway(ABC):
    """Autobiography 산하 챕터 초안. Phase 4 하향식 집필의 단위."""

    @abstractmethod
    async def list_by_autobiography(self, autobiography_id: UUID) -> list[ChapterDraftRecord]:
        """chapter_index 오름차순."""

    @abstractmethod
    async def get(self, chapter_draft_id: UUID) -> ChapterDraftRecord | None: ...

    @abstractmethod
    async def get_by_index(
        self, autobiography_id: UUID, chapter_index: int
    ) -> ChapterDraftRecord | None:
        """직전 챕터 요약 조회(write_chapter)에 사용."""

    @abstractmethod
    async def replace_all(
        self, autobiography_id: UUID, chapters: Sequence[ChapterDraftCreateData]
    ) -> list[ChapterDraftRecord]:
        """목차 후보 재선택 시 이전 챕터 초안을 대체한다(select_toc_candidate, idempotent)."""

    @abstractmethod
    async def save_write_result(
        self, chapter_draft_id: UUID, result: ChapterDraftWriteResult
    ) -> ChapterDraftRecord:
        """write_chapter 파이프라인(시놉시스·본문·팩트체크·근거검증) 산출물을 일괄 반영한다."""

    @abstractmethod
    async def mark_finalized(self, chapter_draft_id: UUID) -> None:
        """finalize_manuscript: 통일성 윤문 패스 완료 후 status=FINALIZED로 전이."""


class CharacterGateway(ABC):
    """등장인물 검토(기획안 Phase 4, 6절 법적 리스크 관리)."""

    @abstractmethod
    async def get_or_create(self, data: CharacterCreateData) -> CharacterRecord:
        """동일 autobiography 내 real_name이 이미 있으면 그 레코드를 반환한다.
        새로 만들 때 display_name 미지정 시 "지인 N" 형태로 자동 채번한다."""

    @abstractmethod
    async def add_mention(
        self, character_id: UUID, *, event_id: UUID | None = None, chapter_draft_id: UUID | None = None
    ) -> None: ...

    @abstractmethod
    async def update_risk_classification(
        self, character_id: UUID, risk_classification: RiskClassification
    ) -> None: ...

    @abstractmethod
    async def list_by_autobiography(self, autobiography_id: UUID) -> list[CharacterRecord]: ...

    @abstractmethod
    async def get(self, character_id: UUID) -> CharacterRecord | None: ...

    @abstractmethod
    async def retain_real_name(self, character_id: UUID, *, notice_version: str) -> CharacterRecord:
        """
        전수 가명화 기본값(opt-out)을 뒤집는다. 유효한 DISCLOSURE_REALNAME 동의가
        있는지는 서비스 레이어(ConsentGateway 확인)가 먼저 검사하고 호출해야 한다 —
        이 메서드 자체는 무조건 real_name_retained=True로 전환하므로, 게이트는
        호출 순서로 지켜진다.
        """


class ConsentGateway(ABC):
    @abstractmethod
    async def create(self, data: ConsentGrantCreateData) -> ConsentGrant: ...

    @abstractmethod
    async def has_active(self, user_id: UUID, consent_type: ConsentType) -> bool:
        """revoked_at IS NULL인 레코드가 하나라도 있는지(사용자 단위 동의 — character_id
        가 null인 레코드만 본다). DATA_COLLECTION 등 인물과 무관한 동의 종류에 쓴다."""

    @abstractmethod
    async def has_active_for_character(
        self, user_id: UUID, character_id: UUID, consent_type: ConsentType
    ) -> bool:
        """revoked_at IS NULL이고 character_id가 정확히 이 인물인 레코드가 있는지.
        DISCLOSURE_REALNAME(인물 단위 실명 유지 동의) 게이트 전용 — 인물 A에 대한
        동의가 인물 B의 실명 유지에 쓰이지 않도록 has_active와 분리했다."""

    @abstractmethod
    async def list_by_user(self, user_id: UUID) -> list[ConsentGrant]: ...


class QuestionGateway(ABC):
    """고정 인터뷰 질문 큐(Question 테이블, app/data/question_bank.py 시드) 접근.

    생애주기를 넘나들며 자유롭게 고르지 않는다 — sequence_order 전역 순서
    (유년기→청년기→장년기→노년기)를 그대로 따라 한 유저에게 다음 질문 하나를
    순서대로 내어준다. life_period는 표시·관리용 메타데이터일 뿐 선택 기준이
    아니다."""

    @abstractmethod
    async def get_next_unasked(self, user_id: UUID) -> QuestionRecord | None:
        """이 유저에게 아직 배정되지 않은 활성 질문 중 sequence_order가 가장 빠른
        것. "배정됨"의 판정 기준은 session_type=FIXED_QUESTION이고 status가
        OPEN이 아닌(즉 완료·건너뜀으로 실제로 다뤄진) 세션들의 question_id
        집합이다. 더 배정할 질문이 없으면(질문 큐를 전부 마쳤으면) None."""
