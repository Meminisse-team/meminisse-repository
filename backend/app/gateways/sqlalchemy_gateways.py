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

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.gateways.dto import (
    AdminAuditLogCreateData,
    AdminAuditLogRecord,
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
    AuditGateway,
    AutobiographyGateway,
    ChapterDraftGateway,
    CharacterGateway,
    ConsentGateway,
    EventGateway,
    InterviewSessionGateway,
    MediaAssetGateway,
    QuestionGateway,
    UserGateway,
)
from app.models import (
    AdminAuditLog,
    Autobiography,
    AutobiographyStatus,
    ChapterDraft,
    ChatLog,
    Character,
    CharacterMention,
    ConsentRecord,
    ConsentType,
    DraftStatus,
    Event,
    EventRelation,
    InterviewSession,
    MediaAsset,
    MessageRole,
    Question,
    RiskClassification,
    SessionStatus,
    SessionType,
    User,
)
from app.models.enums import (
    AssetType,
    EducationLevel,
    LifePeriod,
    MaritalStatus,
    MediaAnalysisTrack,
    UserStage,
)


class SqlAlchemyAuditGateway(AuditGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, data: AdminAuditLogCreateData) -> AdminAuditLogRecord:
        obj = AdminAuditLog(
            admin_id=data.admin_id,
            action=data.action,
            target_user_id=data.target_user_id,
            target_session_id=data.target_session_id,
        )
        self._session.add(obj)
        await self._session.flush()
        return AdminAuditLogRecord(
            id=obj.id, admin_id=obj.admin_id, action=obj.action,
            target_user_id=obj.target_user_id, target_session_id=obj.target_session_id,
            created_at=obj.created_at,
        )

    async def list_recent(self, *, limit: int, offset: int) -> list[AdminAuditLogRecord]:
        result = await self._session.execute(
            select(AdminAuditLog)
            .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            AdminAuditLogRecord(
                id=obj.id, admin_id=obj.admin_id, action=obj.action,
                target_user_id=obj.target_user_id, target_session_id=obj.target_session_id,
                created_at=obj.created_at,
            )
            for obj in result.scalars().all()
        ]


class SqlAlchemyUserGateway(UserGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: UserCreateData) -> UserRecord:
        # id는 새로 생성하지 않는다 — Supabase Auth가 이미 발급한 auth.users.id를
        # 그대로 받아 쓴다(app/services/user_service.py:create_user 참조).
        user = User(
            id=data.id,
            email=data.email,
            name=data.name,
            birth_year=data.birth_year,
            hometown=data.hometown,
            education_level=data.education_level,
            marital_status=data.marital_status,
            has_children=data.has_children,
        )
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

    async def list_all(self, *, limit: int, offset: int) -> list[UserRecord]:
        result = await self._session.execute(
            select(User).order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
        )
        return [_to_user_record(user) for user in result.scalars().all()]

    async def update_email(self, user_id: UUID, new_email: str) -> UserRecord:
        user = await self._session.get(User, user_id)
        if user is None:
            raise KeyError(f"user not found: {user_id}")
        user.email = new_email
        await self._session.flush()
        return _to_user_record(user)

    async def update(
        self,
        user_id: UUID,
        *,
        name: str | None = None,
        birth_year: int | None = None,
        hometown: str | None = None,
        current_stage: UserStage | None = None,
        education_level: EducationLevel | None = None,
        marital_status: MaritalStatus | None = None,
        has_children: bool | None = None,
    ) -> UserRecord:
        user = await self._session.get(User, user_id)
        if user is None:
            raise KeyError(f"user not found: {user_id}")
        if name is not None:
            user.name = name
        if birth_year is not None:
            user.birth_year = birth_year
        if hometown is not None:
            user.hometown = hometown
        if current_stage is not None:
            user.current_stage = current_stage
        if education_level is not None:
            user.education_level = education_level
        if marital_status is not None:
            user.marital_status = marital_status
        if has_children is not None:
            user.has_children = has_children
        await self._session.flush()
        return _to_user_record(user)


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

    async def skip(self, session_id: UUID) -> None:
        session_obj = await self._require_session(session_id)
        session_obj.status = SessionStatus.SKIPPED
        session_obj.completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def list_session_prose_by_user(self, user_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(InterviewSession)
            .where(InterviewSession.user_id == user_id, InterviewSession.session_prose.is_not(None))
            .order_by(InterviewSession.started_at.asc())
        )
        return [s.session_prose for s in result.scalars().all() if s.session_prose]

    async def list_by_user(self, user_id: UUID) -> list[InterviewSessionRecord]:
        # chat_logs는 의도적으로 eager load하지 않는다 — 목록 조회에서 전체 대화까지
        # 함께 내려주면 페이로드가 불필요하게 커진다(interfaces.py 계약 참조).
        # id를 보조 정렬 키로 추가한다 — started_at만으로 정렬하면 짧은 시간 안에
        # 여러 세션이 만들어질 때(빠른 연속 요청, 테스트 등) 동일한 타임스탬프를
        # 가진 행들의 순서가 SQL 표준상 정의되지 않아 쿼리마다 달라질 수 있다
        # (Mock 게이트웨이에서 동일한 근본 원인으로 실제 목록 순서 테스트가
        # 간헐적으로 실패하는 걸 재현·확인, 2026-07-13). id는 UUID(v4)라 생성
        # 순서를 보장하진 않지만, 최소한 매 실행마다 같은(결정적인) 순서를
        # 반환하도록 동률을 깨는 역할은 한다.
        result = await self._session.execute(
            select(InterviewSession)
            .where(InterviewSession.user_id == user_id)
            .order_by(InterviewSession.started_at.desc(), InterviewSession.id.desc())
        )
        return [_to_session_record(s, chat_logs=[]) for s in result.scalars().all()]

    async def apply_user_prose_edit(
        self, session_id: UUID, *, new_prose: str, edited_at: datetime
    ) -> None:
        session_obj = await self._require_session(session_id)
        if session_obj.session_prose_original is None:
            session_obj.session_prose_original = session_obj.session_prose
        session_obj.session_prose = new_prose
        session_obj.prose_last_edited_at = edited_at
        await self._session.flush()

    async def list_stale_completed(self, *, older_than: datetime) -> list[InterviewSessionRecord]:
        result = await self._session.execute(
            select(InterviewSession).where(
                InterviewSession.status == SessionStatus.COMPLETED,
                InterviewSession.session_prose.is_(None),
                InterviewSession.completed_at < older_than,
            )
        )
        return [_to_session_record(s, chat_logs=[]) for s in result.scalars().all()]

    async def list_by_chat_log_content(self, content: str) -> list[InterviewSessionRecord]:
        result = await self._session.execute(
            select(InterviewSession)
            .join(ChatLog, ChatLog.session_id == InterviewSession.id)
            .where(ChatLog.content == content)
            .distinct()
        )
        return [_to_session_record(s, chat_logs=[]) for s in result.scalars().all()]

    async def list_all(self, *, limit: int, offset: int) -> list[InterviewSessionRecord]:
        result = await self._session.execute(
            select(InterviewSession)
            .order_by(InterviewSession.started_at.desc(), InterviewSession.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_to_session_record(s, chat_logs=[]) for s in result.scalars().all()]

    async def list_completed_by_user(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> list[InterviewSessionRecord]:
        result = await self._session.execute(
            select(InterviewSession)
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.status == SessionStatus.COMPLETED,
            )
            .order_by(InterviewSession.started_at.desc(), InterviewSession.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_to_session_record(s, chat_logs=[]) for s in result.scalars().all()]

    async def count_completed_by_user(self, user_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(InterviewSession)
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.status == SessionStatus.COMPLETED,
            )
        )
        return result.scalar_one()

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
        # Layer 1 검증 게이트: verified=False이거나 embedding이 없거나 병합으로 흡수된
        # (duplicate_of_event_id가 채워진) 레코드는 어떤 경우에도 이 WHERE 절을 우회해
        # 반환될 수 없다.
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.embedding.isnot(None),
                Event.duplicate_of_event_id.is_(None),
            )
            .order_by(Event.embedding.cosine_distance(query_embedding))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def search_by_keywords(
        self, *, user_id: UUID, keywords: Sequence[str], limit: int = 10
    ) -> list[EventRecord]:
        if not keywords:
            return []
        keyword_filter = or_(*[Event.one_line_summary.ilike(f"%{kw}%") for kw in keywords])
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
                keyword_filter,
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def list_by_ids(self, event_ids: Sequence[UUID]) -> list[EventRecord]:
        if not event_ids:
            return []
        stmt = (
            select(Event)
            .where(Event.id.in_(event_ids))
            .order_by(Event.importance_score.desc().nullslast())
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def list_unmerged_verified(self, user_id: UUID) -> list[EventRecord]:
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
            )
            .order_by(Event.importance_score.desc().nullslast())
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def list_for_timeline(self, user_id: UUID) -> list[EventRecord]:
        # id 보조 정렬 키: SqlAlchemyInterviewSessionGateway.list_by_user 주석 참조.
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
            )
            .order_by(Event.created_at.desc(), Event.id.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def list_mergeable(self, user_id: UUID) -> list[EventRecord]:
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
                Event.embedding.is_not(None),
            )
            .order_by(Event.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def list_by_session(self, session_id: UUID) -> list[EventRecord]:
        stmt = (
            select(Event)
            .where(
                Event.session_id == session_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
            )
            .order_by(Event.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def delete_by_session(self, session_id: UUID) -> None:
        # event_relations.from_event_id/to_event_id가 ondelete="CASCADE"라(models/event.py)
        # Event 행을 지우면 걸려 있던 관계도 DB 레벨에서 함께 정리된다.
        await self._session.execute(delete(Event).where(Event.session_id == session_id))
        await self._session.flush()

    async def find_merge_candidates(
        self,
        *,
        user_id: UUID,
        exclude_event_id: UUID,
        embedding: list[float],
        max_distance: float,
        limit: int,
    ) -> list[EventRecord]:
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.verified.is_(True),
                Event.duplicate_of_event_id.is_(None),
                Event.embedding.is_not(None),
                Event.id != exclude_event_id,
                Event.embedding.cosine_distance(embedding) < max_distance,
            )
            .order_by(Event.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_to_event_record(obj) for obj in result.scalars().all()]

    async def mark_duplicate(self, event_id: UUID, *, duplicate_of_event_id: UUID) -> None:
        obj = await self._session.get(Event, event_id)
        if obj is None:
            raise KeyError(f"event not found: {event_id}")
        obj.duplicate_of_event_id = duplicate_of_event_id
        await self._session.flush()

    async def count_mentions(self, event_ids: Sequence[UUID]) -> dict[UUID, int]:
        if not event_ids:
            return {}
        result = await self._session.execute(
            select(Event.duplicate_of_event_id, func.count())
            .where(Event.duplicate_of_event_id.in_(event_ids))
            .group_by(Event.duplicate_of_event_id)
        )
        return dict(result.all())

    async def bulk_update_importance(self, updates: Sequence[EventImportanceUpdate]) -> None:
        for update in updates:
            obj = await self._session.get(Event, update.event_id)
            if obj is None:
                raise KeyError(f"event not found: {update.event_id}")
            obj.importance_score = update.importance_score
            obj.importance_signals = update.importance_signals
            obj.life_milestone_category = update.life_milestone_category
        await self._session.flush()

    async def list_all(self, *, limit: int, offset: int) -> list[EventRecord]:
        result = await self._session.execute(
            select(Event).order_by(Event.created_at.desc(), Event.id.desc()).limit(limit).offset(offset)
        )
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

    async def get_by_id(self, media_asset_id: UUID) -> MediaAssetRecord | None:
        obj = await self._session.get(MediaAsset, media_asset_id)
        return _to_media_asset_record(obj) if obj else None

    async def update_analysis(
        self,
        media_asset_id: UUID,
        *,
        analysis_track: MediaAnalysisTrack,
        pre_extracted_labels: dict | None,
        life_period_mapped: LifePeriod | None = None,
        image_caption: str | None = None,
        image_ocr_text: str | None = None,
    ) -> None:
        obj = await self._session.get(MediaAsset, media_asset_id)
        if obj is None:
            raise KeyError(f"media asset not found: {media_asset_id}")
        obj.analysis_track = analysis_track
        obj.pre_extracted_labels = pre_extracted_labels
        obj.image_caption = image_caption
        obj.image_ocr_text = image_ocr_text
        if life_period_mapped is not None:
            obj.life_period_mapped = life_period_mapped
        await self._session.flush()

    async def list_by_user(self, user_id: UUID) -> list[MediaAssetRecord]:
        # id 보조 정렬 키: SqlAlchemyInterviewSessionGateway.list_by_user 주석 참조.
        stmt = (
            select(MediaAsset)
            .where(MediaAsset.user_id == user_id)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_media_asset_record(obj) for obj in result.scalars().all()]

    async def list_uninterviewed(
        self, user_id: UUID, *, life_period: LifePeriod | None
    ) -> list[MediaAssetRecord]:
        already_has_session = select(InterviewSession.linked_media_asset_id).where(
            InterviewSession.session_type == SessionType.PHOTO,
            InterviewSession.linked_media_asset_id.is_not(None),
        )
        stmt = (
            select(MediaAsset)
            .where(
                MediaAsset.user_id == user_id,
                MediaAsset.asset_type == AssetType.IMAGE,
                MediaAsset.life_period_mapped.is_(life_period)
                if life_period is None
                else MediaAsset.life_period_mapped == life_period,
                MediaAsset.id.not_in(already_has_session),
            )
            .order_by(MediaAsset.created_at.asc(), MediaAsset.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_media_asset_record(obj) for obj in result.scalars().all()]


class SqlAlchemyAutobiographyGateway(AutobiographyGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_unfinished_by_user(self, user_id: UUID) -> AutobiographyRecord | None:
        result = await self._session.execute(
            select(Autobiography)
            .where(Autobiography.user_id == user_id, Autobiography.final_content.is_(None))
            .order_by(Autobiography.created_at.desc())
            .limit(1)
        )
        obj = result.scalar_one_or_none()
        return _to_autobiography_record(obj) if obj else None

    async def list_finished_by_user(self, user_id: UUID) -> list[AutobiographyRecord]:
        result = await self._session.execute(
            select(Autobiography)
            .where(Autobiography.user_id == user_id, Autobiography.final_content.is_not(None))
            .order_by(Autobiography.created_at.desc())
        )
        return [_to_autobiography_record(obj) for obj in result.scalars().all()]

    async def get_by_id(self, autobiography_id: UUID) -> AutobiographyRecord | None:
        obj = await self._session.get(Autobiography, autobiography_id)
        return _to_autobiography_record(obj) if obj else None

    async def create(self, user_id: UUID) -> AutobiographyRecord:
        obj = Autobiography(
            user_id=user_id, status=AutobiographyStatus.IN_PROGRESS,
        )
        self._session.add(obj)
        await self._session.flush()
        return _to_autobiography_record(obj)

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
        photo_placements: list[dict] | None = None,
    ) -> AutobiographyRecord:
        obj = await self._session.get(Autobiography, autobiography_id)
        if obj is None:
            raise KeyError(f"autobiography not found: {autobiography_id}")
        if title is not None:
            obj.title = title
        if status is not None:
            obj.status = status
        if consolidated_content is not None:
            obj.consolidated_content = consolidated_content
        if style_bible is not None:
            obj.style_bible = style_bible
        if toc_data is not None:
            obj.toc_data = toc_data
        if book_synopsis is not None:
            obj.book_synopsis = book_synopsis
        if final_content is not None:
            obj.final_content = final_content
        if pdf_url is not None:
            obj.pdf_url = pdf_url
        if photo_placements is not None:
            obj.photo_placements = photo_placements
        await self._session.flush()
        return _to_autobiography_record(obj)

    async def list_all(self, *, limit: int, offset: int) -> list[AutobiographyRecord]:
        result = await self._session.execute(
            select(Autobiography)
            .order_by(Autobiography.created_at.desc(), Autobiography.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_to_autobiography_record(obj) for obj in result.scalars().all()]


class SqlAlchemyChapterDraftGateway(ChapterDraftGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_autobiography(self, autobiography_id: UUID) -> list[ChapterDraftRecord]:
        result = await self._session.execute(
            select(ChapterDraft)
            .where(ChapterDraft.autobiography_id == autobiography_id)
            .order_by(ChapterDraft.chapter_index.asc())
        )
        return [_to_chapter_draft_record(obj) for obj in result.scalars().all()]

    async def list_all(self, *, limit: int, offset: int) -> list[ChapterDraftRecord]:
        result = await self._session.execute(
            select(ChapterDraft)
            .order_by(ChapterDraft.created_at.desc(), ChapterDraft.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_to_chapter_draft_record(obj) for obj in result.scalars().all()]

    async def get(self, chapter_draft_id: UUID) -> ChapterDraftRecord | None:
        obj = await self._session.get(ChapterDraft, chapter_draft_id)
        return _to_chapter_draft_record(obj) if obj else None

    async def get_by_index(
        self, autobiography_id: UUID, chapter_index: int
    ) -> ChapterDraftRecord | None:
        result = await self._session.execute(
            select(ChapterDraft).where(
                ChapterDraft.autobiography_id == autobiography_id,
                ChapterDraft.chapter_index == chapter_index,
            )
        )
        obj = result.scalar_one_or_none()
        return _to_chapter_draft_record(obj) if obj else None

    async def replace_all(
        self, autobiography_id: UUID, chapters: Sequence[ChapterDraftCreateData]
    ) -> list[ChapterDraftRecord]:
        await self._session.execute(
            delete(ChapterDraft).where(ChapterDraft.autobiography_id == autobiography_id)
        )
        objs = [
            ChapterDraft(
                autobiography_id=autobiography_id,
                chapter_index=item.chapter_index,
                title=item.title,
                chapter_synopsis=item.synopsis,
            )
            for item in chapters
        ]
        self._session.add_all(objs)
        await self._session.flush()
        return [_to_chapter_draft_record(obj) for obj in objs]

    async def save_write_result(
        self, chapter_draft_id: UUID, result: ChapterDraftWriteResult
    ) -> ChapterDraftRecord:
        obj = await self._session.get(ChapterDraft, chapter_draft_id)
        if obj is None:
            raise KeyError(f"chapter draft not found: {chapter_draft_id}")
        obj.source_event_ids = result.source_event_ids
        obj.chapter_synopsis = result.chapter_synopsis
        obj.content = result.content
        obj.factcheck_report = result.factcheck_report
        obj.groundedness_report = result.groundedness_report
        obj.status = result.status
        await self._session.flush()
        return _to_chapter_draft_record(obj)

    async def mark_finalized(self, chapter_draft_id: UUID) -> None:
        obj = await self._session.get(ChapterDraft, chapter_draft_id)
        if obj is None:
            raise KeyError(f"chapter draft not found: {chapter_draft_id}")
        obj.status = DraftStatus.FINALIZED
        await self._session.flush()


class SqlAlchemyCharacterGateway(CharacterGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, data: CharacterCreateData) -> CharacterRecord:
        result = await self._session.execute(
            select(Character).where(
                Character.autobiography_id == data.autobiography_id,
                Character.real_name == data.real_name,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return _to_character_record(existing)

        count_result = await self._session.execute(
            select(func.count())
            .select_from(Character)
            .where(Character.autobiography_id == data.autobiography_id)
        )
        next_index = count_result.scalar_one() + 1
        display_name = data.relation_to_user or f"지인 {next_index}"

        obj = Character(
            autobiography_id=data.autobiography_id,
            display_name=display_name,
            real_name=data.real_name,
            relation_to_user=data.relation_to_user,
        )
        self._session.add(obj)
        await self._session.flush()
        return _to_character_record(obj)

    async def add_mention(
        self, character_id: UUID, *, event_id: UUID | None = None, chapter_draft_id: UUID | None = None
    ) -> None:
        self._session.add(
            CharacterMention(character_id=character_id, event_id=event_id, chapter_draft_id=chapter_draft_id)
        )
        await self._session.flush()

    async def update_risk_classification(
        self, character_id: UUID, risk_classification: RiskClassification
    ) -> None:
        obj = await self._session.get(Character, character_id)
        if obj is None:
            raise KeyError(f"character not found: {character_id}")
        obj.risk_classification = risk_classification
        await self._session.flush()

    async def list_by_autobiography(self, autobiography_id: UUID) -> list[CharacterRecord]:
        result = await self._session.execute(
            select(Character)
            .where(Character.autobiography_id == autobiography_id)
            .order_by(Character.created_at)
        )
        return [_to_character_record(obj) for obj in result.scalars().all()]

    async def get(self, character_id: UUID) -> CharacterRecord | None:
        obj = await self._session.get(Character, character_id)
        return _to_character_record(obj) if obj else None

    async def retain_real_name(self, character_id: UUID, *, notice_version: str) -> CharacterRecord:
        obj = await self._session.get(Character, character_id)
        if obj is None:
            raise KeyError(f"character not found: {character_id}")
        obj.real_name_retained = True
        obj.disclosure_notice_version = notice_version
        obj.disclosure_acknowledged_at = datetime.now(timezone.utc)
        await self._session.flush()
        return _to_character_record(obj)


class SqlAlchemyConsentGateway(ConsentGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: ConsentGrantCreateData) -> ConsentGrant:
        obj = ConsentRecord(
            user_id=data.user_id,
            consent_type=data.consent_type,
            notice_version=data.notice_version,
            granted_by=data.granted_by,
            character_id=data.character_id,
        )
        self._session.add(obj)
        await self._session.flush()
        return _to_consent_grant(obj)

    async def has_active(self, user_id: UUID, consent_type: ConsentType) -> bool:
        result = await self._session.execute(
            select(ConsentRecord).where(
                ConsentRecord.user_id == user_id,
                ConsentRecord.consent_type == consent_type,
                ConsentRecord.character_id.is_(None),
                ConsentRecord.revoked_at.is_(None),
            )
        )
        return result.scalars().first() is not None

    async def has_active_for_character(
        self, user_id: UUID, character_id: UUID, consent_type: ConsentType
    ) -> bool:
        result = await self._session.execute(
            select(ConsentRecord).where(
                ConsentRecord.user_id == user_id,
                ConsentRecord.character_id == character_id,
                ConsentRecord.consent_type == consent_type,
                ConsentRecord.revoked_at.is_(None),
            )
        )
        return result.scalars().first() is not None

    async def list_by_user(self, user_id: UUID) -> list[ConsentGrant]:
        # id 보조 정렬 키: SqlAlchemyInterviewSessionGateway.list_by_user 주석 참조.
        result = await self._session.execute(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == user_id)
            .order_by(ConsentRecord.granted_at.desc(), ConsentRecord.id.desc())
        )
        return [_to_consent_grant(obj) for obj in result.scalars().all()]


class SqlAlchemyQuestionGateway(QuestionGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_next_unasked(self, user_id: UUID) -> QuestionRecord | None:
        already_assigned = select(InterviewSession.question_id).where(
            InterviewSession.user_id == user_id,
            InterviewSession.session_type == SessionType.FIXED_QUESTION,
            InterviewSession.status != SessionStatus.OPEN,
            InterviewSession.question_id.is_not(None),
        )
        stmt = (
            select(Question)
            .where(Question.is_active.is_(True))
            .where(Question.id.not_in(already_assigned))
            .order_by(Question.sequence_order.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        question = result.scalar_one_or_none()
        return _to_question_record(question) if question else None

    async def has_assigned_question_in_period(
        self, user_id: UUID, life_period: LifePeriod
    ) -> bool:
        stmt = (
            select(InterviewSession.id)
            .join(Question, InterviewSession.question_id == Question.id)
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.session_type == SessionType.FIXED_QUESTION,
                Question.life_period == life_period,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_id(self, question_id: UUID) -> QuestionRecord | None:
        question = await self._session.get(Question, question_id)
        return _to_question_record(question) if question else None


# --------------------------------------------------------------------------- #
# ORM -> DTO 변환                                                              #
# --------------------------------------------------------------------------- #


def _to_user_record(user: User) -> UserRecord:
    return UserRecord(
        id=user.id, email=user.email, name=user.name,
        birth_year=user.birth_year, hometown=user.hometown, current_stage=user.current_stage,
        role=user.role,
        education_level=user.education_level,
        marital_status=user.marital_status,
        has_children=user.has_children,
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
        session_prose_original=session_obj.session_prose_original,
        prose_last_edited_at=session_obj.prose_last_edited_at,
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
        duplicate_of_event_id=event.duplicate_of_event_id,
        importance_score=event.importance_score,
        importance_signals=event.importance_signals,
        life_milestone_category=event.life_milestone_category,
    )


def _to_media_asset_record(asset: MediaAsset) -> MediaAssetRecord:
    return MediaAssetRecord(
        id=asset.id, user_id=asset.user_id, session_id=asset.session_id,
        s3_key=asset.s3_key, s3_url=asset.s3_url, asset_type=asset.asset_type,
        age_at_time=asset.age_at_time, location_at_time=asset.location_at_time,
        people_at_time=asset.people_at_time, life_period_mapped=asset.life_period_mapped,
        analysis_track=asset.analysis_track, pre_extracted_labels=asset.pre_extracted_labels,
        image_caption=asset.image_caption, image_ocr_text=asset.image_ocr_text,
        user_comment=asset.user_comment, created_at=asset.created_at,
    )


def _to_autobiography_record(autobiography: Autobiography) -> AutobiographyRecord:
    return AutobiographyRecord(
        id=autobiography.id, user_id=autobiography.user_id, title=autobiography.title,
        status=autobiography.status, toc_data=autobiography.toc_data,
        created_at=autobiography.created_at, updated_at=autobiography.updated_at,
        consolidated_content=autobiography.consolidated_content,
        style_bible=autobiography.style_bible,
        book_synopsis=autobiography.book_synopsis,
        final_content=autobiography.final_content,
        pdf_url=autobiography.pdf_url,
        photo_placements=autobiography.photo_placements,
    )


def _to_chapter_draft_record(chapter: ChapterDraft) -> ChapterDraftRecord:
    return ChapterDraftRecord(
        id=chapter.id, autobiography_id=chapter.autobiography_id, chapter_index=chapter.chapter_index,
        title=chapter.title, chapter_synopsis=chapter.chapter_synopsis, content=chapter.content,
        source_event_ids=list(chapter.source_event_ids or []),
        factcheck_report=chapter.factcheck_report, groundedness_report=chapter.groundedness_report,
        status=chapter.status, created_at=chapter.created_at, updated_at=chapter.updated_at,
    )


def _to_character_record(character: Character) -> CharacterRecord:
    return CharacterRecord(
        id=character.id, autobiography_id=character.autobiography_id,
        display_name=character.display_name, real_name=character.real_name,
        relation_to_user=character.relation_to_user, risk_classification=character.risk_classification,
        real_name_retained=character.real_name_retained,
        disclosure_notice_version=character.disclosure_notice_version,
        disclosure_acknowledged_at=character.disclosure_acknowledged_at,
        created_at=character.created_at,
    )


def _to_consent_grant(consent: ConsentRecord) -> ConsentGrant:
    return ConsentGrant(
        id=consent.id, user_id=consent.user_id, consent_type=consent.consent_type,
        notice_version=consent.notice_version, granted_by=consent.granted_by,
        granted_at=consent.granted_at, revoked_at=consent.revoked_at,
        character_id=consent.character_id,
    )


def _to_question_record(question: Question) -> QuestionRecord:
    return QuestionRecord(
        id=question.id, sequence_order=question.sequence_order, title=question.title,
        content=question.content, life_period=question.life_period, is_active=question.is_active,
    )
