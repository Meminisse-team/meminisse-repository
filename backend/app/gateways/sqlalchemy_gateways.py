"""
현재 Supabase(Postgres + pgvector)에 대해 실제로 동작하는 게이트웨이 구현체.

app/services/*.py에 직접 박혀 있던 SQLAlchemy 호출을 이 파일로 옮기고 인터페이스
계약(app/gateways/interfaces.py)을 만족하도록 ORM 객체 <-> DTO 변환만 추가했다.
팀원이 자체 Postgres/pgvector 연동 코드를 가져오면 이 파일의 클래스들을 팀원 버전으로
교체하거나 이 파일 자체를 팀원이 이어받아 고도화하면 된다 — 어느 쪽이든
app/gateways/interfaces.py의 메서드 시그니처만 지키면 서비스 레이어는 영향받지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Autobiography,
    ChatLog,
    Event,
    EventRelation,
    InterviewSession,
    MediaAsset,
    MessageRole,
    Question,  # noqa: F401  (모델 등록 보장을 위한 임포트 — mapper configure에 필요)
    SessionStatus,
    User,
)
from app.models.enums import AutobiographyStatus, MediaAnalysisTrack
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
from app.gateways.interfaces import (
    AutobiographyGateway,
    EventGateway,
    InterviewSessionGateway,
    MediaAssetGateway,
    UserGateway,
)


class SqlAlchemyUserGateway(UserGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: UserCreateData) -> UserRecord:
        user = User(email=data.email, name=data.name, birth_year=data.birth_year, hometown=data.hometown)
        self._session.add(user)
        await self._session.flush()
        return _to_user_record(user)

    async def get_by_id(self, user_id: UUID) -> UserRecord | None:
        user = await self._session.get(User, user_id)
        return _to_user_record(user) if user else None

    async def get_by_email(self, email: str) -> UserRecord | None:
        result = await self._session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        return _to_user_record(user) if user else None


class SqlAlchemyInterviewSessionGateway(InterviewSessionGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: SessionCreateData) -> InterviewSessionRecord:
        session_obj = InterviewSession(
            user_id=data.user_id,
            session_type=data.session_type,
            question_id=data.question_id,
            linked_media_asset_id=data.linked_media_asset_id,
            slots_filled=dict(data.initial_slots_filled),
        )
        self._session.add(session_obj)
        await self._session.flush()
        return _to_session_record(session_obj, chat_logs=[])

    async def get_by_id(self, session_id: UUID) -> InterviewSessionRecord | None:
        result = await self._session.execute(
            select(InterviewSession)
            .where(InterviewSession.id == session_id)
            .options(selectinload(InterviewSession.chat_logs))
        )
        session_obj = result.scalar_one_or_none()
        if session_obj is None:
            return None
        return _to_session_record(session_obj, chat_logs=session_obj.chat_logs)

    async def add_chat_log(
        self, session_id: UUID, *, role: MessageRole, content: str
    ) -> ChatLogRecord:
        count_result = await self._session.execute(
            select(func.count()).select_from(ChatLog).where(ChatLog.session_id == session_id)
        )
        turn_index = count_result.scalar_one()
        chat_log = ChatLog(session_id=session_id, role=role, content=content, turn_index=turn_index)
        self._session.add(chat_log)
        await self._session.flush()
        return _to_chat_log_record(chat_log)

    async def update_slots(
        self, session_id: UUID, *, slots_filled: dict[str, bool], followup_count: int
    ) -> None:
        session_obj = await self._require_session(session_id)
        session_obj.slots_filled = slots_filled
        session_obj.followup_count = followup_count
        await self._session.flush()

    async def set_session_prose(self, session_id: UUID, prose: str) -> None:
        session_obj = await self._require_session(session_id)
        session_obj.session_prose = prose
        await self._session.flush()

    async def complete(self, session_id: UUID) -> None:
        session_obj = await self._require_session(session_id)
        session_obj.status = SessionStatus.COMPLETED
        session_obj.completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def _require_session(self, session_id: UUID) -> InterviewSession:
        session_obj = await self._session.get(InterviewSession, session_id)
        if session_obj is None:
            raise KeyError(f"session not found: {session_id}")
        return session_obj


class SqlAlchemyEventGateway(EventGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: EventCreateData) -> EventRecord:
        return (await self.bulk_create([data]))[0]

    async def bulk_create(self, data: Sequence[EventCreateData]) -> list[EventRecord]:
        objs = [
            Event(
                user_id=item.user_id,
                source_type=item.source_type,
                session_id=item.session_id,
                media_asset_id=item.media_asset_id,
                source_span=item.source_span,
                life_period=item.life_period,
                occurred_at_label=item.occurred_at_label,
                place=item.place,
                people=item.people,
                one_line_summary=item.one_line_summary,
                prose_paragraph=item.prose_paragraph,
                emotion_tag=item.emotion_tag,
                emotion_intensity=item.emotion_intensity,
                emotion_inferred=item.emotion_inferred,
                labels=item.labels,
                confidence=item.confidence,
                verified=item.verified,
                embedding=item.embedding,
            )
            for item in data
        ]
        self._session.add_all(objs)
        await self._session.flush()
        return [_to_event_record(obj) for obj in objs]

    async def bulk_update_embeddings(self, updates: Sequence[tuple[UUID, list[float]]]) -> None:
        for event_id, embedding in updates:
            obj = await self._session.get(Event, event_id)
            if obj is None:
                raise KeyError(f"event not found: {event_id}")
            obj.embedding = embedding
        await self._session.flush()

    async def create_relations(self, relations: Sequence[EventRelationCreateData]) -> None:
        objs = [
            EventRelation(
                from_event_id=r.from_event_id,
                to_event_id=r.to_event_id,
                relation_type=r.relation_type,
            )
            for r in relations
        ]
        self._session.add_all(objs)
        await self._session.flush()

    async def search_verified(
        self, *, user_id: UUID, query_embedding: list[float], limit: int = 10
    ) -> list[EventRecord]:
        # Layer 1 검증 게이트: verified=False이거나 embedding이 없는 레코드는 어떤 경우에도
        # 이 WHERE 절을 우회해 반환될 수 없다.
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.embedding.isnot(None),
            )
            .order_by(Event.embedding.cosine_distance(query_embedding))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]


class SqlAlchemyMediaAssetGateway(MediaAssetGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: MediaAssetCreateData) -> MediaAssetRecord:
        obj = MediaAsset(
            user_id=data.user_id,
            session_id=data.session_id,
            s3_key=data.s3_key,
            s3_url=data.s3_url,
            asset_type=data.asset_type,
            age_at_time=data.age_at_time,
            location_at_time=data.location_at_time,
            people_at_time=data.people_at_time,
            life_period_mapped=data.life_period_mapped,
            user_comment=data.user_comment,
        )
        self._session.add(obj)
        await self._session.flush()
        return _to_media_asset_record(obj)

    async def update_analysis(
        self,
        media_asset_id: UUID,
        *,
        analysis_track: MediaAnalysisTrack,
        pre_extracted_labels: dict | None,
    ) -> None:
        obj = await self._session.get(MediaAsset, media_asset_id)
        if obj is None:
            raise KeyError(f"media asset not found: {media_asset_id}")
        obj.analysis_track = analysis_track
        obj.pre_extracted_labels = pre_extracted_labels
        await self._session.flush()


class SqlAlchemyAutobiographyGateway(AutobiographyGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: UUID) -> AutobiographyRecord | None:
        result = await self._session.execute(
            select(Autobiography).where(Autobiography.user_id == user_id)
        )
        obj = result.scalar_one_or_none()
        return _to_autobiography_record(obj) if obj else None

    async def create(self, user_id: UUID) -> AutobiographyRecord:
        obj = Autobiography(
            user_id=user_id, status=AutobiographyStatus.IN_PROGRESS,
        )
        self._session.add(obj)
        await self._session.flush()
        return _to_autobiography_record(obj)


# --------------------------------------------------------------------------- #
# ORM -> DTO 변환                                                              #
# --------------------------------------------------------------------------- #


def _to_user_record(user: User) -> UserRecord:
    return UserRecord(
        id=user.id, email=user.email, name=user.name,
        birth_year=user.birth_year, hometown=user.hometown, current_stage=user.current_stage,
    )


def _to_chat_log_record(chat_log: ChatLog) -> ChatLogRecord:
    return ChatLogRecord(
        id=chat_log.id, session_id=chat_log.session_id, role=chat_log.role,
        content=chat_log.content, turn_index=chat_log.turn_index, created_at=chat_log.created_at,
    )


def _to_session_record(
    session_obj: InterviewSession, *, chat_logs: Sequence[ChatLog]
) -> InterviewSessionRecord:
    return InterviewSessionRecord(
        id=session_obj.id,
        user_id=session_obj.user_id,
        session_type=session_obj.session_type,
        question_id=session_obj.question_id,
        linked_media_asset_id=session_obj.linked_media_asset_id,
        status=session_obj.status,
        slots_filled=session_obj.slots_filled,
        followup_count=session_obj.followup_count,
        is_must_include=session_obj.is_must_include,
        session_prose=session_obj.session_prose,
        started_at=session_obj.started_at,
        completed_at=session_obj.completed_at,
        chat_logs=[_to_chat_log_record(c) for c in sorted(chat_logs, key=lambda c: c.turn_index)],
    )


def _to_event_record(event: Event) -> EventRecord:
    return EventRecord(
        id=event.id, user_id=event.user_id, source_type=event.source_type,
        session_id=event.session_id, media_asset_id=event.media_asset_id,
        source_span=event.source_span, life_period=event.life_period,
        occurred_at_label=event.occurred_at_label, place=event.place, people=event.people,
        one_line_summary=event.one_line_summary, prose_paragraph=event.prose_paragraph,
        emotion_tag=event.emotion_tag, emotion_intensity=event.emotion_intensity,
        emotion_inferred=event.emotion_inferred, labels=event.labels, confidence=event.confidence,
        verified=event.verified, is_must_include=event.is_must_include,
        embedding=list(event.embedding) if event.embedding is not None else None,
        created_at=event.created_at,
    )


def _to_media_asset_record(asset: MediaAsset) -> MediaAssetRecord:
    return MediaAssetRecord(
        id=asset.id, user_id=asset.user_id, session_id=asset.session_id,
        s3_key=asset.s3_key, s3_url=asset.s3_url, asset_type=asset.asset_type,
        age_at_time=asset.age_at_time, location_at_time=asset.location_at_time,
        people_at_time=asset.people_at_time, life_period_mapped=asset.life_period_mapped,
        analysis_track=asset.analysis_track, pre_extracted_labels=asset.pre_extracted_labels,
        user_comment=asset.user_comment, created_at=asset.created_at,
    )


def _to_autobiography_record(autobiography: Autobiography) -> AutobiographyRecord:
    return AutobiographyRecord(
        id=autobiography.id, user_id=autobiography.user_id, title=autobiography.title,
        status=autobiography.status, toc_data=autobiography.toc_data,
        created_at=autobiography.created_at, updated_at=autobiography.updated_at,
    )
