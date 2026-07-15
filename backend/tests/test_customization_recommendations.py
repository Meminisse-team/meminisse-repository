"""
질문 리스트(app/data/question_bank.py의 suggested_tags) → 자서전 커스터마이징
추천(autobiography_service.get_customization_recommendations) 연동 회귀 테스트.

하이브리드 계약:
- Phase 3(consolidate_autobiography) 이전, 또는 이벤트가 없어 콘텐츠 기반 추천
  자체가 생성되지 않은 경우: 답변을 남긴(=검증된 이벤트가 딸린) 고정 질문들의
  suggested_tags를 모아 app/agents/prompts.py의 _TAG_TO_OPTION으로 정규화한 뒤
  카테고리(말투/구성/컨셉)별 빈도 상위 항목을 추천한다("tag_based"). 답변한 질문이
  하나도 없으면 빈 추천.
- Phase 3 완료 후: consolidate_autobiography가 스타일 바이블 생성 직후 실제 문체·
  사건 내용을 LLM에 보여주고 얻은 추천이 style_bible.recommended_customization에
  저장되어 있으면 그것을 최우선으로 쓴다("content_based") — 태그 기반보다 항상
  우선한다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.agents import prompts
from app.data.question_bank import QUESTION_BANK
from app.gateways.dto import EventCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import EventSourceType, SessionType
from app.schemas.interview import SessionCreate
from app.services import autobiography_service, interview_service


async def _create_user(gateways):
    return await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )


async def _answer_question(gateways, user_id: uuid.UUID):
    """다음 미배정 고정 질문에 대한 세션을 만들고, 그 세션에 연결된 검증된
    이벤트 하나를 심는다 — "이 질문에 실제로 답변이 남았다"는 상태를 흉내낸다.
    반환값은 만들어진 세션(consolidate 경로에서 session_prose를 채울 때 재사용)."""
    session = await interview_service.create_session(
        gateways, user_id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.events.create(
        EventCreateData(
            user_id=user_id,
            source_type=EventSourceType.SESSION_CHAT,
            session_id=session.id,
            one_line_summary="테스트 사건",
            prose_paragraph="테스트 사건에 대한 산문.",
            verified=True,
        )
    )
    return session


@pytest.mark.asyncio
async def test_no_recommendations_before_any_answered_question() -> None:
    """아직 답변한 질문이 하나도 없으면(이벤트 없음) 빈 추천을 돌려준다(태그 기반)."""
    gateways = _build_mock_gateways()
    user = await _create_user(gateways)
    await gateways.commit()

    autobiography = await autobiography_service.get_or_create_autobiography(gateways, user.id)
    recommendations = await autobiography_service.get_customization_recommendations(
        gateways, autobiography.id
    )
    assert recommendations == {
        "tones": [], "structures": [], "concepts": [], "source": "tag_based", "reasoning": None,
    }


@pytest.mark.asyncio
async def test_tag_based_recommendations_reflect_first_question_suggested_tags() -> None:
    """첫 번째 고정 질문(sequence_order=1)에만 답했다면(Phase 3 이전이라 콘텐츠 기반
    추천이 없으므로), 그 질문의 suggested_tags가 가리키는 옵션 키가 그대로
    태그 기반 추천에 나와야 한다."""
    gateways = _build_mock_gateways()
    user = await _create_user(gateways)
    await gateways.commit()

    await _answer_question(gateways, user.id)
    await gateways.commit()

    autobiography = await autobiography_service.get_or_create_autobiography(gateways, user.id)
    recommendations = await autobiography_service.get_customization_recommendations(
        gateways, autobiography.id
    )

    expected = prompts.recommend_customization_keys(QUESTION_BANK[0]["suggested_tags"])
    assert recommendations == {
        "tones": expected["tone"][:2],
        "structures": expected["structure"][:2],
        "concepts": expected["concept"][:2],
        "source": "tag_based",
        "reasoning": None,
    }
    # 첫 질문의 원본 태그(["공간 및 장소 중심", "특정 시기 집중 조명", "소설적 서술체"])가
    # 실제로 무엇을 가리키는지 고정값으로도 검증해 매핑 회귀를 잡는다.
    assert recommendations["tones"] == ["literary"]
    assert recommendations["structures"] == ["geographical"]
    assert recommendations["concepts"] == ["golden_era"]


@pytest.mark.asyncio
async def test_tag_based_recommendations_rank_by_frequency_across_multiple_answered_questions() -> None:
    """여러 질문에 답했다면, 그 질문들의 suggested_tags를 합산해 카테고리당
    상위 2개까지만(select_customization의 1~2개 제약과 맞춤) 추천해야 한다."""
    gateways = _build_mock_gateways()
    user = await _create_user(gateways)
    await gateways.commit()

    for _ in range(5):
        await _answer_question(gateways, user.id)
        await gateways.commit()

    autobiography = await autobiography_service.get_or_create_autobiography(gateways, user.id)
    recommendations = await autobiography_service.get_customization_recommendations(
        gateways, autobiography.id
    )

    assert recommendations["source"] == "tag_based"
    for category in ("tones", "structures", "concepts"):
        assert 0 <= len(recommendations[category]) <= 2

    # 상위 추천이 실제로 유효한 옵션 키(select_customization의 유효성 검증을 통과할 수
    # 있는 값)여야 한다 — 잘못된 매핑이 그대로 새어나가면 select 단계가 400으로 거부한다.
    for tone_key in recommendations["tones"]:
        assert tone_key in prompts.TONE_OPTIONS
    for structure_key in recommendations["structures"]:
        assert structure_key in prompts.STRUCTURE_OPTIONS
    for concept_key in recommendations["concepts"]:
        assert concept_key in prompts.CONCEPT_OPTIONS


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
    return _FakeCompletion("스타일 바이블: 담담하고 사건 중심적인 문체.")


_FAKE_CONTENT_BASED_RESULT = {
    "tones": ["documentary"],
    "structures": ["episodic"],
    "concepts": ["resilience"],
    "reasoning": "위기와 극복을 사실적으로 서술하는 화자의 실제 문체를 근거로 추천합니다.",
}


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    if schema_name == "event_merge_judge":
        return {"same_event": False, "reasoning": "다른 사건으로 판단"}
    if schema_name == "customization_recommendation":
        return _FAKE_CONTENT_BASED_RESULT
    raise AssertionError(f"unexpected schema_name: {schema_name}")


@pytest.mark.asyncio
async def test_content_based_recommendation_is_generated_by_consolidate_and_preferred() -> None:
    """Phase 3(consolidate_autobiography)가 끝나면 스타일 바이블/사건 내용을 근거로 한
    콘텐츠 기반 추천이 style_bible.recommended_customization에 저장되고,
    get_customization_recommendations는 이 시점부터 태그 기반이 아니라 이쪽을
    우선해야 한다 — 답변한 질문의 태그와 다른 값이어도(실제로 다르게 설정) 콘텐츠
    기반 값이 그대로 반환되어야 함을 확인한다."""
    gateways = _build_mock_gateways()
    user = await _create_user(gateways)
    await gateways.commit()

    session = await _answer_question(gateways, user.id)
    await gateways.sessions.set_session_prose(session.id, "그해 겨울은 유난히 힘들었다.")
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
    ):
        autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)

    assert autobiography.style_bible["recommended_customization"] == _FAKE_CONTENT_BASED_RESULT

    recommendations = await autobiography_service.get_customization_recommendations(
        gateways, autobiography.id
    )
    assert recommendations == {
        "tones": ["documentary"],
        "structures": ["episodic"],
        "concepts": ["resilience"],
        "source": "content_based",
        "reasoning": _FAKE_CONTENT_BASED_RESULT["reasoning"],
    }
    # 첫 질문의 태그 기반 결과("literary"/"geographical"/"golden_era")와는 다른 값이므로,
    # 실제로 콘텐츠 기반 경로가 태그 기반을 덮어썼다는 것을 확실히 구분해 보여준다.
    assert recommendations["tones"] != ["literary"]


@pytest.mark.asyncio
async def test_consolidate_without_events_skips_content_based_recommendation() -> None:
    """이벤트가 하나도 없으면(=스타일 바이블도 None) 콘텐츠 기반 추천 자체가
    생성되지 않고, 이후 조회도 태그 기반(빈 추천)으로 남아야 한다."""
    gateways = _build_mock_gateways()
    user = await _create_user(gateways)
    await gateways.commit()

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
    ):
        autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)

    assert autobiography.style_bible is None

    recommendations = await autobiography_service.get_customization_recommendations(
        gateways, autobiography.id
    )
    assert recommendations["source"] == "tag_based"
    assert recommendations == {
        "tones": [], "structures": [], "concepts": [], "source": "tag_based", "reasoning": None,
    }
