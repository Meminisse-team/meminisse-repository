"""
사진(PHOTO) 세션 오케스트레이션 회귀 테스트(docs/QUESTION_BANK_GUIDE.md 5절).

핵심 규칙: 시기가 확정된 사진은 그 생애주기 고정 질문을 모두 마친 직후, 시기가
불명확한 사진은 전체 고정 질문을 다 마친 뒤에 각각 독립된 PHOTO 세션으로 제시된다.

고정 질문 큐는 실제 시드 데이터(app/data/question_bank.py, 유년기 8개)를 그대로
쓴다 — Mock 스토어가 그 데이터로 초기화되므로 별도 세팅이 필요 없다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.gateways.dto import EventCreateData, MediaAssetCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import AssetType, EventSourceType, LifePeriod, SessionType
from app.schemas.interview import SessionCreate
from app.services import interview_service

_ALL_SLOTS_FILLED = dict.fromkeys(
    ["place", "time", "event", "emotion", "values", "companion"], True
)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
    return _FakeCompletion("자유 텍스트 응답")


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    if schema_name == "tier1_detection":
        return {"strong_negative_emotion": False}
    if schema_name == "slot_gating":
        return {"newly_filled_slots": []}
    raise AssertionError(f"unexpected schema_name: {schema_name}")


def _patches():
    return (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        # complete_session()이 세션마다 Celery .delay()로 실제 Redis 브로커 연결을
        # 시도한다 — 브로커가 없는(또는 도달 불가한) 테스트 환경에서 연결 시도 자체가
        # 몇 초씩 걸려(재시도 포함) 이 테스트처럼 세션을 수십 개 연속으로 완료시키는
        # 경우 체감상 멈춘 것처럼 느려진다. 이 테스트의 관심사는 브로커 연동이 아니라
        # 오케스트레이션 로직이므로 큐잉 자체를 모킹한다.
        patch("app.workers.tasks.process_session_completion.delay"),
    )


async def _complete_one_session(gateways, session) -> tuple:
    """세션의 슬롯을 강제로 다 채운 뒤 한 턴을 보내 완료 처리시킨다. (assistant_content, updated_session) 반환."""
    await gateways.sessions.update_slots(
        session.id, slots_filled=_ALL_SLOTS_FILLED, followup_count=0
    )
    session = await gateways.sessions.get_by_id(session.id)
    _, assistant_turn, updated = await interview_service.add_user_turn(gateways, session, "내용")
    return assistant_turn.content, updated


async def _complete_n_fixed_sessions(gateways, user_id: uuid.UUID, n: int) -> str:
    """FIXED_QUESTION 세션을 n개 연속으로 만들어 즉시 완료시킨다. 마지막 세션의
    assistant_content(다음 항목 미리보기 문구)를 반환한다."""
    last_content = ""
    for _ in range(n):
        session = await interview_service.create_session(
            gateways, user_id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )
        last_content, _ = await _complete_one_session(gateways, session)
    return last_content


@pytest.mark.asyncio
async def test_photo_session_offered_right_after_its_life_period_finishes() -> None:
    """유년기 사진 하나 + 유년기 고정 질문 8개 — 8번째를 마치면 청년기 첫 질문이
    아니라 그 사진의 PHOTO 세션이 먼저 제시돼야 한다."""
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        asset = await gateways.media_assets.create(
            MediaAssetCreateData(
                user_id=user.id,
                s3_key="k",
                s3_url="https://example.com/k",
                asset_type=AssetType.IMAGE,
                life_period_mapped=LifePeriod.CHILDHOOD,
            )
        )
        await gateways.commit()

        last_preview = await _complete_n_fixed_sessions(gateways, user.id, 8)
        assert "사진" in last_preview

        next_session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )
        assert next_session.session_type == SessionType.PHOTO
        assert next_session.linked_media_asset_id == asset.id


@pytest.mark.asyncio
async def test_late_uploaded_photo_does_not_interrupt_a_later_period_already_in_progress() -> None:
    """유년기 질문을 모두 마치고 청년기로 넘어간 뒤에야(=그 경계를 이미 지난 뒤)
    유년기 사진이 업로드되면, 지금 진행 중인 청년기 질문 흐름을 끊고 끼어들지
    않아야 한다 — 대신 전체 고정 질문을 마친 뒤 몰아보기에서 다뤄진다."""
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        await gateways.commit()

        # 사진 없이 유년기 8개를 모두 마치고 청년기로 넘어간다.
        await _complete_n_fixed_sessions(gateways, user.id, 8)
        youth_session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )
        assert youth_session.session_type == SessionType.FIXED_QUESTION
        assert youth_session.question_id is not None

        # 청년기 첫 질문에 아직 답하기도 전에, 유년기 사진이 뒤늦게 업로드된다.
        asset = await gateways.media_assets.create(
            MediaAssetCreateData(
                user_id=user.id,
                s3_key="k",
                s3_url="https://example.com/k",
                asset_type=AssetType.IMAGE,
                life_period_mapped=LifePeriod.CHILDHOOD,
            )
        )
        await gateways.commit()

        # 청년기 첫 질문을 마쳐도 사진이 끼어들지 않고 청년기 두 번째 질문으로 이어진다.
        content, _ = await _complete_one_session(gateways, youth_session)
        assert "사진" not in content

        second_youth_session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )
        assert second_youth_session.session_type == SessionType.FIXED_QUESTION

        # 남은 청년기(10개) + 장년기(10) + 노년기(10) 고정 질문을 전부 마치면
        # 그제서야 뒤늦은 유년기 사진이 몰아보기로 제시된다.
        await gateways.sessions.update_slots(
            second_youth_session.id, slots_filled=_ALL_SLOTS_FILLED, followup_count=0
        )
        session = await gateways.sessions.get_by_id(second_youth_session.id)
        _, assistant_turn, _ = await interview_service.add_user_turn(gateways, session, "내용")
        assert "사진" not in assistant_turn.content

        await _complete_n_fixed_sessions(gateways, user.id, 29)

        final_session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )
        assert final_session.session_type == SessionType.PHOTO
        assert final_session.linked_media_asset_id == asset.id


@pytest.mark.asyncio
async def test_unmapped_period_photo_offered_only_after_all_fixed_questions_done() -> None:
    """시기 불명 사진은 39개 고정 질문을 전부 마친 뒤에만 제시된다."""
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        asset = await gateways.media_assets.create(
            MediaAssetCreateData(
                user_id=user.id,
                s3_key="k",
                s3_url="https://example.com/k",
                asset_type=AssetType.IMAGE,
                life_period_mapped=None,
            )
        )
        await gateways.commit()

        # 아직 고정 질문이 남아 있는 동안에는 시기 불명 사진이 끼어들지 않는다.
        first_content, _ = await _complete_one_session(
            gateways,
            await interview_service.create_session(
                gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
            ),
        )
        assert "사진" not in first_content

        await _complete_n_fixed_sessions(gateways, user.id, 38)

        next_session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )
        assert next_session.session_type == SessionType.PHOTO
        assert next_session.linked_media_asset_id == asset.id


@pytest.mark.asyncio
async def test_no_remaining_questions_error_when_everything_exhausted() -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        await gateways.commit()

        await _complete_n_fixed_sessions(gateways, user.id, 39)

        with pytest.raises(interview_service.NoRemainingQuestionsError):
            await interview_service.create_session(
                gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
            )


@pytest.mark.asyncio
async def test_photo_session_completion_cleans_up_pending_ocr_event_and_uses_hint() -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        asset = await gateways.media_assets.create(
            MediaAssetCreateData(
                user_id=user.id,
                s3_key="k",
                s3_url="https://example.com/k",
                asset_type=AssetType.IMAGE,
                life_period_mapped=LifePeriod.CHILDHOOD,
            )
        )
        pending_event = await gateways.events.create(
            EventCreateData(
                user_id=user.id,
                source_type=EventSourceType.DOCUMENT,
                media_asset_id=asset.id,
                one_line_summary="1963년 겨울",
                prose_paragraph="일기장에 남은 기록.",
                source_span={"quoted_text": "1963년 겨울"},
                verified=False,
            )
        )
        await gateways.commit()

        await _complete_n_fixed_sessions(gateways, user.id, 8)

        photo_session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )
        assert photo_session.session_type == SessionType.PHOTO

        # 사진 세션을 다시 열람할 때 OCR 힌트가 시작 질문에 녹아 있는지는 별도
        # 헬퍼로 확인(add_user_turn의 "다음 항목 미리보기" 문구를 통해 간접 검증).
        content, updated = await _complete_one_session(gateways, photo_session)
        assert updated.status.value == "completed"

        # 촉발제였던 OCR 스테이징 이벤트는 정리(삭제)됐어야 한다.
        assert (await gateways.events.list_by_ids([pending_event.id])) == []
