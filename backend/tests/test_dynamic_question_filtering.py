"""동적 질문 필터링(interview_service._question_eligible / _resolve_next_item) 테스트.

핵심 계약: 가입 시 라디오 버튼으로 명시 입력받은 User.education_level/
marital_status/has_children을 기준으로, question_bank.py의 eligibility 조건과
명확히 어긋나는 질문은 사용자에게 보여주지 않고 SKIPPED 세션으로 자동 처리한
뒤 다음 후보로 넘어간다. 그 프로필 필드를 아직 모르면(None) 항상 통과시킨다
(2026-07-16 설계 — 체크박스 온보딩 기반, 대화 추론 아님).
"""

from __future__ import annotations

import uuid

import pytest

from app.gateways.dto import QuestionRecord, SessionCreateData, UserCreateData, UserRecord
from app.gateways.factory import _build_mock_gateways
from app.gateways.mock.store import default_store
from app.models.enums import LifePeriod, MaritalStatus, SessionStatus, SessionType, UserStage
from app.services import interview_service
from app.services.interview_service import _question_eligible


def _fake_question(sequence_order: int) -> QuestionRecord:
    return QuestionRecord(
        id=uuid.uuid4(),
        sequence_order=sequence_order,
        title="t",
        content="c",
        life_period=LifePeriod.ADULTHOOD,
        is_active=True,
    )


async def _make_user(gateways, **profile_kwargs):
    return await gateways.users.create(
        UserCreateData(
            id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터", **profile_kwargs
        )
    )


async def _complete_questions_before(gateways, user_id, sequence_order_cutoff: int) -> None:
    """이 사용자에게 sequence_order < cutoff인 질문을 전부 COMPLETED로 미리
    만들어, get_next_unasked가 cutoff 근방 질문을 바로 다음 후보로 돌려주게
    한다 — 실제 100문항을 하나씩 다 밟지 않고 특정 지점만 테스트하기 위함."""
    questions = sorted(default_store.questions.values(), key=lambda q: q.sequence_order)
    for q in questions:
        if q.sequence_order >= sequence_order_cutoff:
            break
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user_id, session_type=SessionType.FIXED_QUESTION, question_id=q.id)
        )
        await gateways.sessions.complete(session.id)


def test_question_eligible_true_when_no_eligibility_declared() -> None:
    question = _fake_question(sequence_order=1)  # 실제 질문 1번엔 eligibility가 없다.
    user = UserRecord(
        id=uuid.uuid4(), email="a@test.local", name="테스터", birth_year=None,
        hometown=None, current_stage=UserStage.ONBOARDING,
    )
    assert _question_eligible(question, user) is True


def test_question_eligible_true_when_profile_field_unknown() -> None:
    """has_children=None(응답하지 않음)이면 has_children을 요구하는 질문도 통과."""
    question = _fake_question(sequence_order=52)  # "부모가 되던 날" — has_children 필요.
    user = UserRecord(
        id=uuid.uuid4(), email="a@test.local", name="테스터", birth_year=None,
        hometown=None, current_stage=UserStage.ONBOARDING, has_children=None,
    )
    assert _question_eligible(question, user) is True


def test_question_eligible_false_when_profile_explicitly_contradicts() -> None:
    question = _fake_question(sequence_order=52)  # has_children=True 요구.
    user = UserRecord(
        id=uuid.uuid4(), email="a@test.local", name="테스터", birth_year=None,
        hometown=None, current_stage=UserStage.ONBOARDING, has_children=False,
    )
    assert _question_eligible(question, user) is False


def test_question_eligible_requires_one_of_marital_status() -> None:
    question = _fake_question(sequence_order=51)  # marital_status in [married, divorced, widowed].
    single_user = UserRecord(
        id=uuid.uuid4(), email="a@test.local", name="테스터", birth_year=None,
        hometown=None, current_stage=UserStage.ONBOARDING, marital_status=MaritalStatus.SINGLE,
    )
    married_user = UserRecord(
        id=uuid.uuid4(), email="b@test.local", name="테스터", birth_year=None,
        hometown=None, current_stage=UserStage.ONBOARDING, marital_status=MaritalStatus.MARRIED,
    )
    assert _question_eligible(question, single_user) is False
    assert _question_eligible(question, married_user) is True


@pytest.mark.asyncio
async def test_ineligible_questions_are_auto_skipped_and_next_eligible_one_returned() -> None:
    """has_children=False인 사용자에게 52~54번(전부 has_children 필요)은 전부
    건너뛰고, 조건이 없는 55번("나의 집 마련")이 배정돼야 한다."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways, has_children=False)
    await _complete_questions_before(gateways, user.id, sequence_order_cutoff=52)

    item = await interview_service._resolve_next_item(gateways, user.id)

    assert item is not None
    assert item.session_type == SessionType.FIXED_QUESTION
    assert item.question is not None
    assert item.question.sequence_order == 55

    # 52~54번은 사용자에게 보이지 않고 SKIPPED 세션으로 자동 처리됐어야 한다.
    skipped_orders = sorted(
        default_store.questions[s.question_id].sequence_order
        for s in default_store.sessions.values()
        if s.user_id == user.id and s.status == SessionStatus.SKIPPED and s.question_id is not None
    )
    assert skipped_orders == [52, 53, 54]
    # 사용자에게 한 번도 안 보였으니 오프닝 chat_log가 없어야 한다.
    for s in default_store.sessions.values():
        if s.user_id == user.id and s.status == SessionStatus.SKIPPED:
            assert s.chat_logs == []


@pytest.mark.asyncio
async def test_eligible_question_is_assigned_normally_without_skipping() -> None:
    gateways = _build_mock_gateways()
    user = await _make_user(gateways, has_children=True)
    await _complete_questions_before(gateways, user.id, sequence_order_cutoff=52)

    item = await interview_service._resolve_next_item(gateways, user.id)

    assert item is not None
    assert item.question is not None
    assert item.question.sequence_order == 52

    skipped = [
        s for s in default_store.sessions.values()
        if s.user_id == user.id and s.status == SessionStatus.SKIPPED
    ]
    assert skipped == []


@pytest.mark.asyncio
async def test_unknown_profile_does_not_trigger_skipping() -> None:
    """has_children을 아예 응답하지 않은(None) 사용자는 모르는 채로 안전하게
    질문을 그대로 받는다 — 필터링은 명확히 어긋난다고 확인된 경우에만 동작."""
    gateways = _build_mock_gateways()
    user = await _make_user(gateways)  # has_children/marital_status 전부 None.
    await _complete_questions_before(gateways, user.id, sequence_order_cutoff=52)

    item = await interview_service._resolve_next_item(gateways, user.id)

    assert item is not None
    assert item.question is not None
    assert item.question.sequence_order == 52
