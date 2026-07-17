"""
Phase 4 챕터 집필의 팩트체크/근거검증 자동 재시도(autobiography_service.write_chapter)
회귀 테스트.

배경: factcheck_report/groundedness_report는 계산만 되고 검토 화면 어디에도 노출되지
않아, 사실상 아무도 안 보는 죽은 데이터였다(2026-07-16). 이를 실제로 쓰는 첫 단계로,
플래그가 하나라도 있으면 같은 자료로 한 번 더 집필을 시도해 flag가 더 적은 쪽을
채택한다 — 재시도가 오히려 나빠질 수도 있으므로 "무조건 재시도 결과 사용"이 아니라
"더 나은 쪽 채택"이 계약이다.

근거검증(groundedness)은 이제 로컬 NLI가 아니라 Solar LLM 판정
(schema_name="groundedness_judge")이므로, 이 테스트의 fake structured_completion도
그 스키마 호출을 가로채 챕터 본문(_BAD_CONTENT/_GOOD_CONTENT)에 따라 플래그 유무를
결정한다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.gateways.dto import EventCreateData, SessionCreateData
from app.gateways.factory import Gateways, _build_mock_gateways
from app.models.enums import EventSourceType, SessionType
from app.schemas.user import UserCreate
from app.services import autobiography_service, user_service


async def _fake_admin_create_user(*, email: str, password: str, user_metadata: dict) -> uuid.UUID:
    return uuid.uuid4()


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


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
        gateways, UserCreate(email="retry@example.com", name="테스터", password="test-password-123")
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
    return chapters[0].id


@pytest.mark.asyncio
async def test_write_chapter_retries_once_and_keeps_better_result_when_flagged() -> None:
    """1차 집필 결과가 근거검증에 걸리면 재시도하고, 재시도 결과가 더 적게
    flag되면 그걸 채택해야 한다."""
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeCompletion("챕터 시놉시스")  # _generate_chapter_synopsis
        if call_count["n"] == 2:
            return _FakeCompletion(_BAD_CONTENT)  # 1차 집필 — 근거 없음
        return _FakeCompletion(_GOOD_CONTENT)  # 재시도 — 근거 있음

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        chapter_draft_id = await _prepare_chapter(gateways)
        call_count["n"] = 0  # _prepare_chapter(consolidate/toc) 안에서도 chat_completion이 불려서 여기서부터 다시 센다

        chapter = await autobiography_service.write_chapter(gateways, chapter_draft_id)

        assert chapter.content == _GOOD_CONTENT
        assert chapter.groundedness_report["flags"] == []
        assert call_count["n"] == 3  # 시놉시스 1회 + 집필 2회(1차 + 재시도)


@pytest.mark.asyncio
async def test_write_chapter_does_not_retry_when_no_flags() -> None:
    """처음부터 flag가 없으면 재시도(추가 Solar 호출) 자체를 하지 않아야 한다 —
    불필요한 비용·지연을 만들지 않기 위함."""
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        call_count["n"] += 1
        return _FakeCompletion("챕터 시놉시스" if call_count["n"] == 1 else _GOOD_CONTENT)

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        chapter_draft_id = await _prepare_chapter(gateways)
        call_count["n"] = 0  # _prepare_chapter(consolidate/toc) 안에서도 chat_completion이 불려서 여기서부터 다시 센다

        chapter = await autobiography_service.write_chapter(gateways, chapter_draft_id)

        assert chapter.content == _GOOD_CONTENT
        assert call_count["n"] == 2  # 시놉시스 1회 + 집필 1회. 재시도 없음.


@pytest.mark.asyncio
async def test_write_chapter_keeps_original_when_retry_is_not_better() -> None:
    """재시도해도 flag 개수가 줄지 않으면(오히려 같거나 늘면) 원래 결과를 그대로
    유지해야 한다 — "무조건 재시도 결과로 교체"가 아니라 "더 나을 때만 교체"."""
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeCompletion("챕터 시놉시스")
        return _FakeCompletion(_BAD_CONTENT)  # 1차, 재시도 모두 근거 없는 문장

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        chapter_draft_id = await _prepare_chapter(gateways)
        call_count["n"] = 0  # _prepare_chapter(consolidate/toc) 안에서도 chat_completion이 불려서 여기서부터 다시 센다

        chapter = await autobiography_service.write_chapter(gateways, chapter_draft_id)

        assert chapter.content == _BAD_CONTENT
        assert len(chapter.groundedness_report["flags"]) == 1
        assert call_count["n"] == 3  # 재시도는 시도했지만(2회 집필) 결과는 원본 유지
