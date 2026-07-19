"""
Phase 4 챕터 집필의 외과적 수리 루프(autobiography_service.write_chapter) 회귀 테스트.

배경: 예전에는 팩트체크/근거검증 플래그가 뜨면 플래그 내용을 전달하지 않은 채 같은
프롬프트로 챕터 전체를 다시 쓰는 블라인드 재집필 1회를 했다(2026-07-16 도입) —
무엇이 문제였는지 모델이 모른 채 다시 쓰는 동전 던지기라 수렴 보장이 없고, 멀쩡한
부분에 새 환각이 생길 수 있었다. 지금은 플래그된 문장·사유·근거를 명시한 수리
프롬프트(CHAPTER_REPAIR_SYSTEM_PROMPT)로 그 문장만 고치고, 결과가 실제로 더 나을
때만(플래그 감소) 채택한다(2026-07-17 교체).

챕터 시놉시스는 이제 select_toc_candidate가 목차 확정 시점에 미리 생성·저장하므로
write_chapter 안에서는 시놉시스 생성 호출이 없다 — 각 테스트의 chat_completion 호출
순서는 [1차 집필 → (플래그 시) 수리]다.

write_chapter에 분량 확장·검수(교열) 패스가 추가되면서(2026-07-18) 이 테스트들의
짧은 본문은 두 패스를 항상 트리거하게 됐다 — 이 파일의 관심사는 수리 루프
배선뿐이므로, 가짜 chat_completion이 두 보조 패스 호출을 식별해(_is_auxiliary_pass)
빈 응답으로 무력화하고(빈 응답 = 원본 유지가 두 패스의 계약) 호출 수에서도 뺀다.
확장·검수 패스 자체는 test_literary_quality_improvements.py가 검증한다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.agents import prompts
from app.gateways.dto import EventCreateData, SessionCreateData
from app.gateways.factory import Gateways, _build_mock_gateways
from app.models.enums import EventSourceType, SessionType
from app.schemas.user import UserCreate
from app.services import autobiography_service, user_service


async def _fake_admin_create_user(*, email: str, password: str, user_metadata: dict) -> uuid.UUID:
    return uuid.uuid4()


async def _fake_groundedness_api_check(*, context: str, answer: str) -> str:
    # 2차 게이트가 판정자 플래그를 철회하지 않도록 고정 — 이 테스트의 관심사는
    # 수리 루프 배선이지 게이트 판정이 아니다(게이트 자체는
    # test_chapter_groundedness_check.py가 검증).
    return "notGrounded"


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def _is_auxiliary_pass(messages: list[dict]) -> bool:
    """검수(교열) 패스 호출인지 — 이 파일의 테스트들은 수리 루프만 검증하므로
    이 보조 패스는 빈 응답으로 무력화한다(파일 docstring 참조). 분량 확장
    패스는 2026-07-19 폐기됐다(prompts.py 참조 — 챕터당 순차 Solar 호출을
    늘리기만 하고 분량 미달을 보장하지도 못했다)."""
    system = messages[0]["content"]
    return "교열하세요" in system


_STRUCTURED_RESPONSES = {
    "event_merge_judge": {"same_event": False, "reasoning": "다른 사건으로 판단"},
    "customization_recommendation": {
        "tones": ["confessional"], "structures": ["chronological"], "concepts": ["family"],
        "reasoning": "이 조합이 잘 어울립니다.",
    },
    "toc_generation": {
        "candidates": [
            {"chapters": [{"chapter_index": 1, "title": "1장. 어린 시절", "theme_keywords": ["어린시절"]}]},
        ]
    },
    "book_title": {"title": "부산의 여름"},
    "fact_reextraction": {"facts": []},  # 이 테스트는 근거검증(groundedness)만 겨냥한다.
    "ner_extraction": {"people": []},
}

_BAD_CONTENT = "지어낸 문장입니다."
_GOOD_CONTENT = "나는 부산에서 태어나 자랐다."


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    if schema_name == "groundedness_judge":
        # 챕터 본문이 user 메시지에 그대로 들어간다(build_groundedness_judge_prompt).
        user_content = messages[1]["content"]
        if _BAD_CONTENT in user_content:
            return {"flags": [{"sentence": _BAD_CONTENT, "reason": "근거 사건에 없는 창작 문장"}]}
        return {"flags": []}
    return _STRUCTURED_RESPONSES[schema_name]


async def _seed_user_with_one_event(gateways: Gateways):
    user = await user_service.create_user(
        gateways, UserCreate(email="repair@example.com", name="테스터", password="test-password-123")
    )
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.set_session_prose(session.id, "나는 부산에서 태어나 자랐다.")
    await gateways.sessions.complete(session.id)
    events = await gateways.events.bulk_create(
        [
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT,
                one_line_summary="부산 출생", prose_paragraph="나는 부산에서 태어나 자랐다.",
                verified=True, emotion_intensity=3,
            ),
        ]
    )
    await gateways.events.bulk_update_embeddings([(events[0].id, [1.0, 0.0, 0.0])])
    await gateways.commit()
    return user


async def _prepare_chapter(gateways: Gateways):
    """write_chapter를 호출할 수 있는 상태(목차 선택까지 완료)로 만든다."""
    user = await _seed_user_with_one_event(gateways)
    autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)
    autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography.id)
    autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)
    chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
    # select_toc_candidate가 챕터 시놉시스를 미리 생성해 저장했어야 한다 —
    # write_chapter가 이 값을 읽어 시놉시스 생성 호출을 건너뛰는 전제.
    assert chapters[0].chapter_synopsis
    return chapters[0].id


@pytest.mark.asyncio
async def test_write_chapter_repairs_flagged_content_with_repair_prompt() -> None:
    """1차 집필 결과가 근거검증에 걸리면 수리 프롬프트(플래그된 문장·사유·근거
    명시)로 고치고, 수리 결과의 플래그가 더 적으면 그걸 채택해야 한다."""
    call_count = {"n": 0}
    captured_messages: list[list[dict]] = []

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        if _is_auxiliary_pass(messages):
            return _FakeCompletion("")
        call_count["n"] += 1
        captured_messages.append(messages)
        if call_count["n"] == 1:
            return _FakeCompletion(_BAD_CONTENT)  # 1차 집필 — 근거 없음
        return _FakeCompletion(_GOOD_CONTENT)  # 수리 — 근거 있는 문장으로 교정

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_api_check),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        chapter_draft_id = await _prepare_chapter(gateways)
        call_count["n"] = 0  # _prepare_chapter(consolidate/toc/시놉시스)에서도 chat_completion이 불려서 여기서부터 다시 센다
        captured_messages.clear()

        chapter = await autobiography_service.write_chapter(gateways, chapter_draft_id)

        assert chapter.content == _GOOD_CONTENT
        assert chapter.groundedness_report["flags"] == []
        assert call_count["n"] == 2  # 집필 1회 + 수리 1회

        # 2번째 호출은 블라인드 재집필이 아니라 수리 프롬프트여야 하고, 플래그된
        # 문장과 사유가 실제로 전달되어야 한다.
        repair_messages = captured_messages[1]
        assert repair_messages[0]["content"] == prompts.CHAPTER_REPAIR_SYSTEM_PROMPT
        assert _BAD_CONTENT in repair_messages[1]["content"]
        assert "근거 사건에 없는 창작 문장" in repair_messages[1]["content"]


@pytest.mark.asyncio
async def test_write_chapter_does_not_repair_when_no_flags() -> None:
    """처음부터 flag가 없으면 수리(추가 Solar 호출) 자체를 하지 않아야 한다 —
    불필요한 비용·지연을 만들지 않기 위함."""
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        if _is_auxiliary_pass(messages):
            return _FakeCompletion("")
        call_count["n"] += 1
        return _FakeCompletion(_GOOD_CONTENT)

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_api_check),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        chapter_draft_id = await _prepare_chapter(gateways)
        call_count["n"] = 0  # _prepare_chapter(consolidate/toc/시놉시스)에서도 chat_completion이 불려서 여기서부터 다시 센다

        chapter = await autobiography_service.write_chapter(gateways, chapter_draft_id)

        assert chapter.content == _GOOD_CONTENT
        assert call_count["n"] == 1  # 집필 1회뿐. 수리 없음.


@pytest.mark.asyncio
async def test_write_chapter_keeps_original_when_repair_is_not_better() -> None:
    """수리해도 flag 개수가 줄지 않으면(같거나 늘면) 원래 결과를 유지하고 루프를
    중단해야 한다 — "무조건 수리 결과로 교체"가 아니라 "더 나을 때만 교체". 잔여
    플래그는 리포트에 남아 검토 화면에서 확인할 수 있어야 한다."""
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        if _is_auxiliary_pass(messages):
            return _FakeCompletion("")
        call_count["n"] += 1
        return _FakeCompletion(_BAD_CONTENT)  # 1차 집필도, 수리 결과도 근거 없는 문장

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_api_check),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        chapter_draft_id = await _prepare_chapter(gateways)
        call_count["n"] = 0  # _prepare_chapter(consolidate/toc/시놉시스)에서도 chat_completion이 불려서 여기서부터 다시 센다

        chapter = await autobiography_service.write_chapter(gateways, chapter_draft_id)

        assert chapter.content == _BAD_CONTENT
        assert len(chapter.groundedness_report["flags"]) == 1
        assert call_count["n"] == 2  # 수리는 시도했지만(집필 1 + 수리 1) 개선이 없어 중단·원본 유지


@pytest.mark.asyncio
async def test_write_chapter_strips_citation_tags_and_saves_clean_content() -> None:
    """집필 규약상 본문에 섞여 나오는 근거 태그([E1]...)는 저장 전에 반드시
    제거되어야 한다 — PDF 조판이 content를 그대로 인쇄하기 때문."""
    tagged_content = "나는 부산에서 태어나 자랐다. [E1]\n\n그 시절이 그립다."

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        if _is_auxiliary_pass(messages):
            return _FakeCompletion("")
        return _FakeCompletion(tagged_content)

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_api_check),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        chapter_draft_id = await _prepare_chapter(gateways)

        chapter = await autobiography_service.write_chapter(gateways, chapter_draft_id)

        assert "[E1]" not in chapter.content
        assert "나는 부산에서 태어나 자랐다." in chapter.content
        assert "그 시절이 그립다." in chapter.content
