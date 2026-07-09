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
    ChatLogRecord,
    EventCreateData,
    EventRecord,
    EventRelationCreateData,
    InterviewSessionRecord,
    MediaAssetCreateData,
    MediaAssetRecord,
    SessionCreateData,
    UserCreateData,
    UserRecord,
)
from app.models.enums import MediaAnalysisTrack, MessageRole


class ObjectStorageGateway(ABC):
    """Layer 0(불변 원천) 원본 파일 저장소. 실제 구현은 S3, Mock 구현은 인메모리 dict."""

    @abstractmethod
    async def put_object(self, key: str, data: bytes, *, content_type: str) -> str:
        """객체를 저장하고 접근 가능한 URL을 반환한다."""

    @abstractmethod
    async def get_presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        """제한 시간 동안 유효한 접근 URL을 발급한다."""


class UserGateway(ABC):
    @abstractmethod
    async def create(self, data: UserCreateData) -> UserRecord: ...

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> UserRecord | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> UserRecord | None: ...


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
    async def complete(self, session_id: UUID) -> None:
        """status=COMPLETED, completed_at=now로 전이한다."""


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
        RAG용 시맨틱 검색. 반드시 verified=True AND embedding IS NOT NULL인 레코드만
        반환해야 한다(Layer 1 검증 게이트). 이 조건은 호출부가 아니라 구현체 내부에서
        강제되어야 하며, 어떤 인자를 넘기더라도 우회할 수 없어야 한다.
        """


class MediaAssetGateway(ABC):
    @abstractmethod
    async def create(self, data: MediaAssetCreateData) -> MediaAssetRecord: ...

    @abstractmethod
    async def update_analysis(
        self,
        media_asset_id: UUID,
        *,
        analysis_track: MediaAnalysisTrack,
        pre_extracted_labels: dict | None,
    ) -> None:
        """Phase 1 듀얼 트랙 분석 결과(Document Parse 산출물)를 기록한다."""


class AutobiographyGateway(ABC):
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> AutobiographyRecord | None: ...

    @abstractmethod
    async def create(self, user_id: UUID) -> AutobiographyRecord: ...
