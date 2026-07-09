"""Mock 게이트웨이: 실제 Postgres/S3 없이 인터페이스 계약대로 동작하는 인메모리 구현체."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

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
    ObjectStorageGateway,
    UserGateway,
)
from app.gateways.mock.store import MockStore
from app.models.enums import AutobiographyStatus, MessageRole, SessionStatus, UserStage


class MockObjectStorage(ObjectStorageGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def put_object(self, key: str, data: bytes, *, content_type: str) -> str:
        self._store.objects[key] = data
        return f"mock://objects/{key}"

    async def get_presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        if key not in self._store.objects:
            raise KeyError(f"object not found in mock store: {key}")
        return f"mock://objects/{key}?expires_in={expires_in}"


class MockUserGateway(UserGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def create(self, data: UserCreateData) -> UserRecord:
        user = UserRecord(
            id=uuid.uuid4(),
            email=data.email,
            name=data.name,
            birth_year=data.birth_year,
            hometown=data.hometown,
            current_stage=UserStage.ONBOARDING,
        )
        self._store.users[user.id] = user
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> UserRecord | None:
        return self._store.users.get(user_id)

    async def get_by_email(self, email: str) -> UserRecord | None:
        return next((u for u in self._store.users.values() if u.email == email), None)


class MockInterviewSessionGateway(InterviewSessionGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def create(self, data: SessionCreateData) -> InterviewSessionRecord:
        now = datetime.now(timezone.utc)
        session = InterviewSessionRecord(
            id=uuid.uuid4(),
            user_id=data.user_id,
            session_type=data.session_type,
            question_id=data.question_id,
            linked_media_asset_id=data.linked_media_asset_id,
            status=SessionStatus.OPEN,
            slots_filled=dict(data.initial_slots_filled),
            followup_count=0,
            is_must_include=False,
            session_prose=None,
            started_at=now,
            completed_at=None,
            chat_logs=[],
        )
        self._store.sessions[session.id] = session
        return session

    async def get_by_id(self, session_id: uuid.UUID) -> InterviewSessionRecord | None:
        return self._store.sessions.get(session_id)

    async def add_chat_log(
        self, session_id: uuid.UUID, *, role: MessageRole, content: str
    ) -> ChatLogRecord:
        session = self._require_session(session_id)
        chat_log = ChatLogRecord(
            id=uuid.uuid4(),
            session_id=session_id,
            role=role,
            content=content,
            turn_index=len(session.chat_logs),
            created_at=datetime.now(timezone.utc),
        )
        session.chat_logs.append(chat_log)
        return chat_log

    async def update_slots(
        self, session_id: uuid.UUID, *, slots_filled: dict[str, bool], followup_count: int
    ) -> None:
        session = self._require_session(session_id)
        session.slots_filled = slots_filled
        session.followup_count = followup_count

    async def set_session_prose(self, session_id: uuid.UUID, prose: str) -> None:
        self._require_session(session_id).session_prose = prose

    async def complete(self, session_id: uuid.UUID) -> None:
        session = self._require_session(session_id)
        session.status = SessionStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc)

    def _require_session(self, session_id: uuid.UUID) -> InterviewSessionRecord:
        session = self._store.sessions.get(session_id)
        if session is None:
            raise KeyError(f"session not found in mock store: {session_id}")
        return session


class MockEventGateway(EventGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def create(self, data: EventCreateData) -> EventRecord:
        return (await self.bulk_create([data]))[0]

    async def bulk_create(self, data: Sequence[EventCreateData]) -> list[EventRecord]:
        created: list[EventRecord] = []
        for item in data:
            event = EventRecord(
                id=uuid.uuid4(),
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
                is_must_include=False,
                embedding=item.embedding,
                created_at=datetime.now(timezone.utc),
            )
            self._store.events[event.id] = event
            created.append(event)
        return created

    async def bulk_update_embeddings(
        self, updates: Sequence[tuple[uuid.UUID, list[float]]]
    ) -> None:
        for event_id, embedding in updates:
            event = self._store.events.get(event_id)
            if event is None:
                raise KeyError(f"event not found in mock store: {event_id}")
            event.embedding = embedding

    async def create_relations(self, relations: Sequence[EventRelationCreateData]) -> None:
        self._store.event_relations.extend(relations)

    async def search_verified(
        self, *, user_id: uuid.UUID, query_embedding: list[float], limit: int = 10
    ) -> list[EventRecord]:
        candidates = [
            event
            for event in self._store.events.values()
            if event.user_id == user_id and event.verified and event.embedding is not None
        ]
        scored = [
            (event, _dot_product(query_embedding, event.embedding))
            for event in candidates
            if event.embedding is not None
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [event for event, _score in scored[:limit]]


class MockMediaAssetGateway(MediaAssetGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def create(self, data: MediaAssetCreateData) -> MediaAssetRecord:
        asset = MediaAssetRecord(
            id=uuid.uuid4(),
            user_id=data.user_id,
            session_id=data.session_id,
            s3_key=data.s3_key,
            s3_url=data.s3_url,
            asset_type=data.asset_type,
            age_at_time=data.age_at_time,
            location_at_time=data.location_at_time,
            people_at_time=data.people_at_time,
            life_period_mapped=data.life_period_mapped,
            analysis_track=None,
            pre_extracted_labels=None,
            user_comment=data.user_comment,
            created_at=datetime.now(timezone.utc),
        )
        self._store.media_assets[asset.id] = asset
        return asset

    async def update_analysis(
        self,
        media_asset_id: uuid.UUID,
        *,
        analysis_track,
        pre_extracted_labels: dict | None,
    ) -> None:
        asset = self._store.media_assets.get(media_asset_id)
        if asset is None:
            raise KeyError(f"media asset not found in mock store: {media_asset_id}")
        asset.analysis_track = analysis_track
        asset.pre_extracted_labels = pre_extracted_labels


class MockAutobiographyGateway(AutobiographyGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def get_by_user_id(self, user_id: uuid.UUID) -> AutobiographyRecord | None:
        return self._store.autobiographies_by_user.get(user_id)

    async def create(self, user_id: uuid.UUID) -> AutobiographyRecord:
        now = datetime.now(timezone.utc)
        autobiography = AutobiographyRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            title=None,
            status=AutobiographyStatus.IN_PROGRESS,
            toc_data=None,
            created_at=now,
            updated_at=now,
        )
        self._store.autobiographies_by_user[user_id] = autobiography
        return autobiography


def _dot_product(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
