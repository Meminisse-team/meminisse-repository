"""
의존성 주입(DI)의 유일한 조립 지점.

서비스 레이어와 라우터는 이 파일의 `Gateways` 번들만 주입받는다. 어떤 백엔드가
실제로 붙어 있는지는 `settings.GATEWAY_BACKEND` 하나로 결정되며, 팀원이 완성된
Postgres/S3 연동 코드를 가져왔을 때 실제로 손대야 하는 곳은 원칙적으로 이 파일뿐이다.

### 팀원의 완성 코드를 갈아 끼우는 법

1. 팀원의 게이트웨이 클래스가 `app/gateways/interfaces.py`의 ABC를 상속하도록
   맞춘다(메서드 시그니처만 맞으면 내부 구현은 SQLAlchemy든 raw asyncpg든 자유).
2. 아래 `_build_postgres_gateways`에서 `SqlAlchemy*Gateway` 임포트를
   팀원 클래스 임포트로 바꾼다. 서비스 코드는 한 줄도 건드릴 필요 없다.
3. S3도 마찬가지로 `_build_postgres_gateways`(혹은 별도 스토리지 팩토리 함수)의
   `S3ObjectStorageGateway` 자리만 교체한다.

즉 실제 교체 범위는 이 파일의 임포트문 및 생성자 호출 몇 줄이다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.database import AsyncSessionLocal
from app.gateways.interfaces import (
    AuditGateway,
    AutobiographyGateway,
    ChapterDraftGateway,
    CharacterGateway,
    ConsentGateway,
    EventGateway,
    InterviewSessionGateway,
    MediaAssetGateway,
    ObjectStorageGateway,
    QuestionGateway,
    UserGateway,
)


@dataclass
class Gateways:
    users: UserGateway
    sessions: InterviewSessionGateway
    events: EventGateway
    media_assets: MediaAssetGateway
    autobiographies: AutobiographyGateway
    chapters: ChapterDraftGateway
    characters: CharacterGateway
    consents: ConsentGateway
    storage: ObjectStorageGateway
    questions: QuestionGateway
    audit: AuditGateway
    _commit: Callable[[], Coroutine[Any, Any, None]]

    async def commit(self) -> None:
        await self._commit()


async def _noop_commit() -> None:
    """Mock 백엔드는 쓰기 즉시 인메모리에 반영되므로 커밋이 필요 없다."""


def _build_mock_gateways() -> Gateways:
    from app.gateways.mock.gateways import (
        MockAuditGateway,
        MockAutobiographyGateway,
        MockChapterDraftGateway,
        MockCharacterGateway,
        MockConsentGateway,
        MockEventGateway,
        MockInterviewSessionGateway,
        MockMediaAssetGateway,
        MockObjectStorage,
        MockQuestionGateway,
        MockUserGateway,
    )
    from app.gateways.mock.store import default_store

    return Gateways(
        users=MockUserGateway(default_store),
        sessions=MockInterviewSessionGateway(default_store),
        events=MockEventGateway(default_store),
        media_assets=MockMediaAssetGateway(default_store),
        autobiographies=MockAutobiographyGateway(default_store),
        chapters=MockChapterDraftGateway(default_store),
        characters=MockCharacterGateway(default_store),
        consents=MockConsentGateway(default_store),
        storage=MockObjectStorage(default_store),
        questions=MockQuestionGateway(default_store),
        audit=MockAuditGateway(default_store),
        _commit=_noop_commit,
    )


def _build_postgres_gateways(session) -> Gateways:  # noqa: ANN001 (AsyncSession)
    from app.gateways.s3_gateway import S3ObjectStorageGateway
    from app.gateways.sqlalchemy_gateways import (
        SqlAlchemyAuditGateway,
        SqlAlchemyAutobiographyGateway,
        SqlAlchemyChapterDraftGateway,
        SqlAlchemyCharacterGateway,
        SqlAlchemyConsentGateway,
        SqlAlchemyEventGateway,
        SqlAlchemyInterviewSessionGateway,
        SqlAlchemyMediaAssetGateway,
        SqlAlchemyQuestionGateway,
        SqlAlchemyUserGateway,
    )

    return Gateways(
        users=SqlAlchemyUserGateway(session),
        sessions=SqlAlchemyInterviewSessionGateway(session),
        events=SqlAlchemyEventGateway(session),
        media_assets=SqlAlchemyMediaAssetGateway(session),
        autobiographies=SqlAlchemyAutobiographyGateway(session),
        chapters=SqlAlchemyChapterDraftGateway(session),
        characters=SqlAlchemyCharacterGateway(session),
        consents=SqlAlchemyConsentGateway(session),
        storage=S3ObjectStorageGateway(),
        questions=SqlAlchemyQuestionGateway(session),
        audit=SqlAlchemyAuditGateway(session),
        _commit=session.commit,
    )


@asynccontextmanager
async def gateways_context() -> AsyncGenerator[Gateways, None]:
    """FastAPI 밖(Celery 태스크, 스크립트, 테스트)에서도 쓸 수 있는 범용 진입점."""
    if settings.GATEWAY_BACKEND == "mock":
        yield _build_mock_gateways()
        return

    async with AsyncSessionLocal() as session:
        try:
            yield _build_postgres_gateways(session)
        except Exception:
            await session.rollback()
            raise


async def get_gateways() -> AsyncGenerator[Gateways, None]:
    """FastAPI Depends()용 래퍼."""
    async with gateways_context() as gateways:
        yield gateways
