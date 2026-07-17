"""자서전 생성 프롬프트 개선(narrative_arc/connecting_thread, 챕터 레시피,
few-shot 예시 주입, 미리보기 순차 스트리밍) 회귀 테스트.

배경: TOC 생성이 "사건 군집화"만 지시해 책 전체를 관통하는 서사 아크나 챕터 간
연결을 요구하지 않았고, 각 옵션(TONE/STRUCTURE/CONCEPT)의 example 필드가
실제 생성 프롬프트에 전혀 주입되지 않았으며, 직전 챕터 "요약"이 실제 요약이
아니라 마지막 1000자를 그냥 자른 것이었다. 이 테스트는 그 개선이 실제로
반영됐는지 확인한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.agents import prompts
from app.gateways.dto import AutobiographyRecord
from app.models.enums import AutobiographyStatus
from app.services import autobiography_service


def _make_autobiography(*, toc_data: dict | None) -> AutobiographyRecord:
    now = datetime.now(timezone.utc)
    return AutobiographyRecord(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title=None,
        status=AutobiographyStatus.IN_PROGRESS,
        toc_data=toc_data,
        created_at=now,
        updated_at=now,
    )


# --------------------------------------------------------------------------- #
# TOC 스키마 — narrative_arc/connecting_thread 필수 필드                       #
# --------------------------------------------------------------------------- #


def test_toc_generation_schema_requires_narrative_arc_and_connecting_thread() -> None:
    candidate_schema = prompts.TOC_GENERATION_SCHEMA["properties"]["candidates"]["items"]
    assert "narrative_arc" in candidate_schema["required"]

    chapter_schema = candidate_schema["properties"]["chapters"]["items"]
    assert "connecting_thread" in chapter_schema["required"]


def test_toc_text_includes_narrative_arc_and_connecting_thread() -> None:
    selected_toc = {
        "narrative_arc": "가난했던 유년기에서 자수성가한 노년기까지의 성장담.",
        "chapters": [
            {
                "chapter_index": 1,
                "title": "1장. 가난한 유년기",
                "theme_keywords": ["가난", "유년기"],
                "connecting_thread": "이 결핍이 2장의 첫 도전으로 이어진다.",
            }
        ],
    }
    text = autobiography_service._toc_text(selected_toc)
    assert "가난했던 유년기에서 자수성가한 노년기까지의 성장담." in text
    assert "이 결핍이 2장의 첫 도전으로 이어진다." in text


def test_toc_text_tolerates_legacy_toc_without_new_fields() -> None:
    """narrative_arc/connecting_thread가 없는 구버전 toc_data(이번 변경 이전에
    생성된 자서전)도 예외 없이 렌더링돼야 한다."""
    selected_toc = {"chapters": [{"chapter_index": 1, "title": "1장", "theme_keywords": []}]}
    text = autobiography_service._toc_text(selected_toc)
    assert "1장" in text


# --------------------------------------------------------------------------- #
# _chapter_connecting_thread — toc_data에서 챕터별 연결고리 조회                #
# --------------------------------------------------------------------------- #


def test_chapter_connecting_thread_found() -> None:
    autobiography = _make_autobiography(
        toc_data={
            "selected_candidate_index": 0,
            "candidates": [
                {
                    "narrative_arc": "...",
                    "chapters": [
                        {"chapter_index": 1, "title": "1장", "connecting_thread": "연결고리 A"},
                        {"chapter_index": 2, "title": "2장", "connecting_thread": "연결고리 B"},
                    ],
                }
            ],
        }
    )
    assert autobiography_service._chapter_connecting_thread(autobiography, 2) == "연결고리 B"


def test_chapter_connecting_thread_none_when_toc_missing_or_unselected() -> None:
    assert autobiography_service._chapter_connecting_thread(_make_autobiography(toc_data=None), 1) is None
    assert (
        autobiography_service._chapter_connecting_thread(
            _make_autobiography(toc_data={"candidates": [], "selected_candidate_index": None}), 1
        )
        is None
    )


def test_chapter_connecting_thread_none_when_chapter_not_found() -> None:
    autobiography = _make_autobiography(
        toc_data={
            "selected_candidate_index": 0,
            "candidates": [{"chapters": [{"chapter_index": 1, "title": "1장"}]}],
        }
    )
    assert autobiography_service._chapter_connecting_thread(autobiography, 99) is None


# --------------------------------------------------------------------------- #
# 직전 챕터 "요약" — 실제 LLM 레시피로 교체됐는지                                #
# --------------------------------------------------------------------------- #


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


@pytest.mark.asyncio
async def test_previous_chapter_summary_uses_recap_prompt_not_raw_tail_slice() -> None:
    from app.gateways.dto import ChapterDraftCreateData, SessionCreateData
    from app.gateways.factory import _build_mock_gateways
    from app.models.enums import EventSourceType, SessionType
    from app.schemas.user import UserCreate
    from app.services import user_service

    long_content = "가" * 2000  # 마지막 1000자 슬라이스와 확실히 구분되는 내용
    recap_text = "1장에서는 유년기의 가난을 다뤘고, 희망을 품은 채로 끝났다."

    captured_messages: list[list[dict]] = []

    async def _fake_chat_completion(messages, **kwargs):
        captured_messages.append(messages)
        return _FakeCompletion(recap_text)

    async def _fake_admin_create_user(*, email, password, user_metadata):
        return uuid.uuid4()

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        user = await user_service.create_user(
            gateways, UserCreate(email="recap@example.com", name="테스터", password="test-password-123")
        )
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
        )
        await gateways.commit()
        autobiography = await gateways.autobiographies.create(user.id)
        chapters = await gateways.chapters.replace_all(
            autobiography.id,
            [
                ChapterDraftCreateData(chapter_index=1, title="1장"),
                ChapterDraftCreateData(chapter_index=2, title="2장"),
            ],
        )
        await gateways.chapters.save_write_result(
            chapters[0].id,
            __import__("app.gateways.dto", fromlist=["ChapterDraftWriteResult"]).ChapterDraftWriteResult(
                source_event_ids=[],
                chapter_synopsis="시놉시스",
                content=long_content,
                factcheck_report={"flags": []},
                groundedness_report={"flags": []},
                status=__import__("app.models.enums", fromlist=["DraftStatus"]).DraftStatus.DRAFT,
            ),
        )
        await gateways.commit()

        summary = await autobiography_service._previous_chapter_summary(gateways, autobiography.id, 2)

    assert summary == recap_text
    assert summary != long_content[-1000:]  # 예전 방식(단순 절단)과 달라야 한다
    # CHAPTER_RECAP_SYSTEM_PROMPT가 실제로 쓰였는지 확인.
    assert captured_messages[0][0]["content"] == prompts.CHAPTER_RECAP_SYSTEM_PROMPT
    assert captured_messages[0][1]["content"] == long_content


# --------------------------------------------------------------------------- #
# few-shot example 주입 — instruction뿐 아니라 example도 프롬프트에 실제로      #
# 포함되는지                                                                    #
# --------------------------------------------------------------------------- #


def test_sample_preview_prompt_injects_few_shot_examples() -> None:
    messages = prompts.build_sample_preview_prompt(
        tone_key="witty",
        structure_key="episodic",
        concept_key="resilience",
        style_bible="스타일 바이블",
        event_summaries="사건 요약",
    )
    user_content = messages[1]["content"]
    assert prompts.TONE_OPTIONS["witty"]["example"] in user_content
    assert prompts.STRUCTURE_OPTIONS["episodic"]["example"] in user_content
    assert prompts.CONCEPT_OPTIONS["resilience"]["example"] in user_content


def test_customized_chapter_writing_prompt_injects_few_shot_examples() -> None:
    messages = prompts.build_customized_chapter_writing_prompt(
        style_bible="스타일 바이블",
        book_synopsis="시놉시스",
        chapter_synopsis="챕터 시놉시스",
        previous_chapter_summary=None,
        retrieved_event_paragraphs=["사건 문단"],
        tone_key="literary",
        concept_key="golden_era",
    )
    system_content = messages[0]["content"]
    assert prompts.TONE_OPTIONS["literary"]["example"] in system_content
    assert prompts.CONCEPT_OPTIONS["golden_era"]["example"] in system_content


def test_customized_unity_revision_prompt_injects_few_shot_examples() -> None:
    messages = prompts.build_customized_unity_revision_prompt(
        style_bible="스타일 바이블",
        full_manuscript="전체 원고",
        tone_key="essay",
        concept_key="philosophical",
    )
    system_content = messages[0]["content"]
    assert prompts.TONE_OPTIONS["essay"]["example"] in system_content
    assert prompts.CONCEPT_OPTIONS["philosophical"]["example"] in system_content


def test_customized_toc_prompt_injects_structure_example() -> None:
    messages = prompts.build_customized_toc_prompt(
        event_summaries_with_scores="사건 요약", structure_key="geographical"
    )
    system_content = messages[0]["content"]
    assert prompts.STRUCTURE_OPTIONS["geographical"]["example"] in system_content


# --------------------------------------------------------------------------- #
# 8개 미리보기 순차 스트리밍 — placeholder 먼저 커밋 후 하나씩 채워지는지         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_sample_previews_commits_placeholders_before_filling() -> None:
    from app.gateways.dto import EventCreateData, SessionCreateData
    from app.gateways.factory import _build_mock_gateways
    from app.models.enums import EventSourceType, SessionType
    from app.schemas.user import UserCreate
    from app.services import user_service

    call_count = {"n": 0}

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        call_count["n"] += 1
        return {"preview_text": f"미리보기 {call_count['n']}"}

    async def _fake_admin_create_user(*, email, password, user_metadata):
        return uuid.uuid4()

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        user = await user_service.create_user(
            gateways, UserCreate(email="preview@example.com", name="테스터", password="test-password-123")
        )
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
        )
        await gateways.events.create(
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT,
                one_line_summary="사건", prose_paragraph="문단", verified=True,
            )
        )
        autobiography = await gateways.autobiographies.create(user.id)
        autobiography = await gateways.autobiographies.update(
            autobiography.id,
            style_bible={
                "content": "스타일 바이블",
                "customization": {"tones": ["plain", "witty"], "structures": ["chronological"], "concepts": ["family"]},
            },
        )
        await gateways.commit()

        # commit()이 호출될 때마다 그 시점의 previews 상태(자리표시자 개수, 채워진
        # 개수)를 기록해 "8개 자리표시자 먼저 → 하나씩 채움" 순서를 검증한다.
        snapshots: list[tuple[int, int]] = []
        original_commit = gateways.commit

        async def _spying_commit():
            await original_commit()
            current = await gateways.autobiographies.get_by_id(autobiography.id)
            previews = ((current.style_bible or {}).get("customization") or {}).get("previews") or []
            filled = sum(1 for p in previews if not p.get("is_generating"))
            snapshots.append((len(previews), filled))

        gateways.commit = _spying_commit

        await autobiography_service.generate_sample_previews(gateways, autobiography.id)

    # 첫 commit: 자리표시자 2개(1개 톤 x 1개 구성 x 2개 컨셉 조합... 실제로는
    # tones=2 x structures=1 x concepts=1 = 2개), 아직 하나도 안 채워짐.
    assert snapshots[0] == (2, 0)
    # 마지막 commit: 전부 채워짐.
    assert snapshots[-1] == (2, 2)
    # 중간에 점진적으로 채워지는 시점이 존재해야 한다(한꺼번에 0→2로 뛰지 않음).
    filled_progression = [filled for _, filled in snapshots]
    assert filled_progression == sorted(filled_progression)  # 단조 비감소
    assert 1 in filled_progression  # 1개만 채워진 중간 상태가 실제로 존재
