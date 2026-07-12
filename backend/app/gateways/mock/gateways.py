"""Mock 게이트웨이: 실제 Postgres/S3 없이 인터페이스 계약대로 동작하는 인메모리 구현체."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

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
from app.gateways.interfaces import (
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
from app.gateways.mock.store import MockStore
from app.models.enums import (
    AutobiographyStatus,
    ConsentType,
    DraftStatus,
    MessageRole,
    RiskClassification,
    SessionStatus,
    SessionType,
    UserStage,
)


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

    async def get_object(self, key: str) -> bytes:
        if key not in self._store.objects:
            raise KeyError(f"object not found in mock store: {key}")
        return self._store.objects[key]


class MockUserGateway(UserGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def create(self, data: UserCreateData) -> UserRecord:
        user = UserRecord(
            id=data.id,
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

    async def update(
        self,
        user_id: uuid.UUID,
        *,
        name: str | None = None,
        birth_year: int | None = None,
        hometown: str | None = None,
        current_stage: UserStage | None = None,
    ) -> UserRecord:
        user = self._store.users.get(user_id)
        if user is None:
            raise KeyError(f"user not found in mock store: {user_id}")
        if name is not None:
            user.name = name
        if birth_year is not None:
            user.birth_year = birth_year
        if hometown is not None:
            user.hometown = hometown
        if current_stage is not None:
            user.current_stage = current_stage
        return user


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

    async def list_session_prose_by_user(self, user_id: uuid.UUID) -> list[str]:
        sessions = sorted(
            (s for s in self._store.sessions.values() if s.user_id == user_id and s.session_prose),
            key=lambda s: s.started_at,
        )
        return [s.session_prose for s in sessions if s.session_prose]

    async def list_by_user(self, user_id: uuid.UUID) -> list[InterviewSessionRecord]:
        sessions = [s for s in self._store.sessions.values() if s.user_id == user_id]
        # sort(key=..., reverse=True) 대신 "오름차순 정렬 + reverse()" 조합을 쓴다 —
        # 둘 다 안정 정렬(stable)이지만 결과가 다르다. started_at이 완전히 동일한
        # 레코드가 여럿이면(테스트처럼 매우 빠르게 연속 생성될 때 실제로 발생 —
        # Windows의 datetime.now() 해상도가 요청보다 거칠 수 있다), reverse=True는
        # 동일 키 그룹 안에서 "원본(삽입) 순서를 그대로" 유지해 가장 먼저 만든 게
        # 앞에 남는다 — "최신순" 계약과 반대가 되어 실제로 목록 순서 테스트가
        # 간헐적으로 실패했다(2026-07-13, test_list_sessions_only_returns_own_
        # sessions_newest_first 재현). 오름차순 정렬 뒤 뒤집으면 동일 키 그룹도
        # "나중에 생성된 게 먼저"로 결정적으로 뒤집힌다.
        sessions.sort(key=lambda s: s.started_at)
        sessions.reverse()
        return sessions

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
            event = self._require_event(event_id)
            event.embedding = embedding

    async def create_relations(self, relations: Sequence[EventRelationCreateData]) -> None:
        self._store.event_relations.extend(relations)

    async def search_verified(
        self, *, user_id: uuid.UUID, query_embedding: list[float], limit: int = 10
    ) -> list[EventRecord]:
        candidates = [
            event
            for event in self._store.events.values()
            if event.user_id == user_id
            and event.verified
            and event.embedding is not None
            and event.duplicate_of_event_id is None
        ]
        scored = [(event, _dot_product(query_embedding, event.embedding)) for event in candidates]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [event for event, _score in scored[:limit]]

    async def search_by_keywords(
        self, *, user_id: uuid.UUID, keywords: Sequence[str], limit: int = 10
    ) -> list[EventRecord]:
        if not keywords:
            return []
        matches = [
            event
            for event in self._store.events.values()
            if event.user_id == user_id
            and event.verified
            and event.duplicate_of_event_id is None
            and any(kw.lower() in event.one_line_summary.lower() for kw in keywords)
        ]
        return matches[:limit]

    async def list_by_ids(self, event_ids: Sequence[uuid.UUID]) -> list[EventRecord]:
        events = [self._store.events[eid] for eid in event_ids if eid in self._store.events]
        events.sort(
            key=lambda e: (e.importance_score is None, -(e.importance_score or 0)),
        )
        return events

    async def list_unmerged_verified(self, user_id: uuid.UUID) -> list[EventRecord]:
        events = [
            event
            for event in self._store.events.values()
            if event.user_id == user_id and event.verified and event.duplicate_of_event_id is None
        ]
        events.sort(key=lambda e: (e.importance_score is None, -(e.importance_score or 0)))
        return events

    async def list_for_timeline(self, user_id: uuid.UUID) -> list[EventRecord]:
        events = [
            event
            for event in self._store.events.values()
            if event.user_id == user_id and event.verified and event.duplicate_of_event_id is None
        ]
        # "오름차순 정렬 + reverse()" 이유: MockInterviewSessionGateway.list_by_user
        # 주석 참조 — sort(reverse=True)는 동일 created_at일 때 삽입 순서를 그대로
        # 유지해 "최신순" 계약이 깨질 수 있다.
        events.sort(key=lambda e: e.created_at)
        events.reverse()
        return events

    async def list_mergeable(self, user_id: uuid.UUID) -> list[EventRecord]:
        events = [
            event
            for event in self._store.events.values()
            if event.user_id == user_id
            and event.verified
            and event.duplicate_of_event_id is None
            and event.embedding is not None
        ]
        events.sort(key=lambda e: e.created_at)
        return events

    async def find_merge_candidates(
        self,
        *,
        user_id: uuid.UUID,
        exclude_event_id: uuid.UUID,
        embedding: list[float],
        max_distance: float,
        limit: int,
    ) -> list[EventRecord]:
        scored = [
            (event, _cosine_distance(embedding, event.embedding))
            for event in self._store.events.values()
            if event.user_id == user_id
            and event.verified
            and event.duplicate_of_event_id is None
            and event.embedding is not None
            and event.id != exclude_event_id
        ]
        scored = [(event, distance) for event, distance in scored if distance < max_distance]
        scored.sort(key=lambda pair: pair[1])
        return [event for event, _distance in scored[:limit]]

    async def mark_duplicate(self, event_id: uuid.UUID, *, duplicate_of_event_id: uuid.UUID) -> None:
        self._require_event(event_id).duplicate_of_event_id = duplicate_of_event_id

    async def count_mentions(self, event_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        id_set = set(event_ids)
        counts: dict[uuid.UUID, int] = {}
        for event in self._store.events.values():
            if event.duplicate_of_event_id in id_set:
                counts[event.duplicate_of_event_id] = counts.get(event.duplicate_of_event_id, 0) + 1
        return counts

    async def bulk_update_importance(self, updates: Sequence[EventImportanceUpdate]) -> None:
        for update in updates:
            event = self._require_event(update.event_id)
            event.importance_score = update.importance_score
            event.importance_signals = update.importance_signals
            event.life_milestone_category = update.life_milestone_category

    def _require_event(self, event_id: uuid.UUID) -> EventRecord:
        event = self._store.events.get(event_id)
        if event is None:
            raise KeyError(f"event not found in mock store: {event_id}")
        return event


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

    async def get_by_id(self, media_asset_id: uuid.UUID) -> MediaAssetRecord | None:
        return self._store.media_assets.get(media_asset_id)

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

    async def list_by_user(self, user_id: uuid.UUID) -> list[MediaAssetRecord]:
        assets = [a for a in self._store.media_assets.values() if a.user_id == user_id]
        # "오름차순 정렬 + reverse()" 이유: MockInterviewSessionGateway.list_by_user 참조.
        assets.sort(key=lambda a: a.created_at)
        assets.reverse()
        return assets


class MockAutobiographyGateway(AutobiographyGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def get_by_user_id(self, user_id: uuid.UUID) -> AutobiographyRecord | None:
        return next((a for a in self._store.autobiographies.values() if a.user_id == user_id), None)

    async def get_by_id(self, autobiography_id: uuid.UUID) -> AutobiographyRecord | None:
        return self._store.autobiographies.get(autobiography_id)

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
        self._store.autobiographies[autobiography.id] = autobiography
        return autobiography

    async def update(
        self,
        autobiography_id: uuid.UUID,
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
        autobiography = self._store.autobiographies.get(autobiography_id)
        if autobiography is None:
            raise KeyError(f"autobiography not found in mock store: {autobiography_id}")
        if title is not None:
            autobiography.title = title
        if status is not None:
            autobiography.status = status
        if consolidated_content is not None:
            autobiography.consolidated_content = consolidated_content
        if style_bible is not None:
            autobiography.style_bible = style_bible
        if toc_data is not None:
            autobiography.toc_data = toc_data
        if book_synopsis is not None:
            autobiography.book_synopsis = book_synopsis
        if final_content is not None:
            autobiography.final_content = final_content
        if pdf_url is not None:
            autobiography.pdf_url = pdf_url
        autobiography.updated_at = datetime.now(timezone.utc)
        return autobiography


class MockChapterDraftGateway(ChapterDraftGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def list_by_autobiography(self, autobiography_id: uuid.UUID) -> list[ChapterDraftRecord]:
        chapters = [
            c for c in self._store.chapter_drafts.values() if c.autobiography_id == autobiography_id
        ]
        chapters.sort(key=lambda c: c.chapter_index)
        return chapters

    async def get(self, chapter_draft_id: uuid.UUID) -> ChapterDraftRecord | None:
        return self._store.chapter_drafts.get(chapter_draft_id)

    async def get_by_index(
        self, autobiography_id: uuid.UUID, chapter_index: int
    ) -> ChapterDraftRecord | None:
        return next(
            (
                c
                for c in self._store.chapter_drafts.values()
                if c.autobiography_id == autobiography_id and c.chapter_index == chapter_index
            ),
            None,
        )

    async def replace_all(
        self, autobiography_id: uuid.UUID, chapters: Sequence[ChapterDraftCreateData]
    ) -> list[ChapterDraftRecord]:
        stale_ids = [
            cid
            for cid, chapter in self._store.chapter_drafts.items()
            if chapter.autobiography_id == autobiography_id
        ]
        for cid in stale_ids:
            del self._store.chapter_drafts[cid]

        now = datetime.now(timezone.utc)
        created: list[ChapterDraftRecord] = []
        for item in chapters:
            chapter = ChapterDraftRecord(
                id=uuid.uuid4(),
                autobiography_id=autobiography_id,
                chapter_index=item.chapter_index,
                title=item.title,
                chapter_synopsis=None,
                content=None,
                source_event_ids=[],
                factcheck_report=None,
                groundedness_report=None,
                status=DraftStatus.DRAFT,
                created_at=now,
                updated_at=now,
            )
            self._store.chapter_drafts[chapter.id] = chapter
            created.append(chapter)
        return created

    async def save_write_result(
        self, chapter_draft_id: uuid.UUID, result: ChapterDraftWriteResult
    ) -> ChapterDraftRecord:
        chapter = self._store.chapter_drafts.get(chapter_draft_id)
        if chapter is None:
            raise KeyError(f"chapter draft not found in mock store: {chapter_draft_id}")
        chapter.source_event_ids = result.source_event_ids
        chapter.chapter_synopsis = result.chapter_synopsis
        chapter.content = result.content
        chapter.factcheck_report = result.factcheck_report
        chapter.groundedness_report = result.groundedness_report
        chapter.status = result.status
        chapter.updated_at = datetime.now(timezone.utc)
        return chapter

    async def mark_finalized(self, chapter_draft_id: uuid.UUID) -> None:
        chapter = self._store.chapter_drafts.get(chapter_draft_id)
        if chapter is None:
            raise KeyError(f"chapter draft not found in mock store: {chapter_draft_id}")
        chapter.status = DraftStatus.FINALIZED
        chapter.updated_at = datetime.now(timezone.utc)


class MockCharacterGateway(CharacterGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def get_or_create(self, data: CharacterCreateData) -> CharacterRecord:
        existing = next(
            (
                c
                for c in self._store.characters.values()
                if c.autobiography_id == data.autobiography_id and c.real_name == data.real_name
            ),
            None,
        )
        if existing is not None:
            return existing

        next_index = (
            sum(1 for c in self._store.characters.values() if c.autobiography_id == data.autobiography_id)
            + 1
        )
        display_name = data.relation_to_user or f"지인 {next_index}"
        character = CharacterRecord(
            id=uuid.uuid4(),
            autobiography_id=data.autobiography_id,
            display_name=display_name,
            real_name=data.real_name,
            relation_to_user=data.relation_to_user,
            risk_classification=RiskClassification.NONE,
            real_name_retained=False,
            disclosure_notice_version=None,
            disclosure_acknowledged_at=None,
            created_at=datetime.now(timezone.utc),
        )
        self._store.characters[character.id] = character
        return character

    async def add_mention(
        self, character_id: uuid.UUID, *, event_id: uuid.UUID | None = None, chapter_draft_id: uuid.UUID | None = None
    ) -> None:
        self._store.character_mentions.append((character_id, event_id, chapter_draft_id))

    async def update_risk_classification(
        self, character_id: uuid.UUID, risk_classification: RiskClassification
    ) -> None:
        self._require_character(character_id).risk_classification = risk_classification

    async def list_by_autobiography(self, autobiography_id: uuid.UUID) -> list[CharacterRecord]:
        characters = [
            c for c in self._store.characters.values() if c.autobiography_id == autobiography_id
        ]
        characters.sort(key=lambda c: c.created_at)
        return characters

    async def get(self, character_id: uuid.UUID) -> CharacterRecord | None:
        return self._store.characters.get(character_id)

    async def retain_real_name(self, character_id: uuid.UUID, *, notice_version: str) -> CharacterRecord:
        character = self._require_character(character_id)
        character.real_name_retained = True
        character.disclosure_notice_version = notice_version
        character.disclosure_acknowledged_at = datetime.now(timezone.utc)
        return character

    def _require_character(self, character_id: uuid.UUID) -> CharacterRecord:
        character = self._store.characters.get(character_id)
        if character is None:
            raise KeyError(f"character not found in mock store: {character_id}")
        return character


class MockConsentGateway(ConsentGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def create(self, data: ConsentGrantCreateData) -> ConsentGrant:
        grant = ConsentGrant(
            id=uuid.uuid4(),
            user_id=data.user_id,
            consent_type=data.consent_type,
            notice_version=data.notice_version,
            granted_by=data.granted_by,
            granted_at=datetime.now(timezone.utc),
            revoked_at=None,
            character_id=data.character_id,
        )
        self._store.consents[grant.id] = grant
        return grant

    async def has_active(self, user_id: uuid.UUID, consent_type: ConsentType) -> bool:
        return any(
            grant.user_id == user_id
            and grant.consent_type == consent_type
            and grant.character_id is None
            and grant.revoked_at is None
            for grant in self._store.consents.values()
        )

    async def has_active_for_character(
        self, user_id: uuid.UUID, character_id: uuid.UUID, consent_type: ConsentType
    ) -> bool:
        return any(
            grant.user_id == user_id
            and grant.character_id == character_id
            and grant.consent_type == consent_type
            and grant.revoked_at is None
            for grant in self._store.consents.values()
        )

    async def list_by_user(self, user_id: uuid.UUID) -> list[ConsentGrant]:
        grants = [g for g in self._store.consents.values() if g.user_id == user_id]
        # "오름차순 정렬 + reverse()" 이유: MockInterviewSessionGateway.list_by_user 참조.
        grants.sort(key=lambda g: g.granted_at)
        grants.reverse()
        return grants


class MockQuestionGateway(QuestionGateway):
    def __init__(self, store: MockStore) -> None:
        self._store = store

    async def get_next_unasked(self, user_id: uuid.UUID) -> QuestionRecord | None:
        assigned_question_ids = {
            s.question_id
            for s in self._store.sessions.values()
            if s.user_id == user_id
            and s.session_type == SessionType.FIXED_QUESTION
            and s.status != SessionStatus.OPEN
            and s.question_id is not None
        }
        candidates = [
            q
            for q in self._store.questions.values()
            if q.is_active and q.id not in assigned_question_ids
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda q: q.sequence_order)


def _dot_product(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Upstage 임베딩은 정규화되어 있다고 가정(문서 참조) — 내적으로 코사인 유사도를
    바로 얻고, pgvector의 <=> 연산자(1 - cosine_similarity)와 동일한 스케일로 맞춘다."""
    return 1.0 - _dot_product(a, b)
