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


async def _seed_two_chapters(gateways, *, first_chapter_synopsis: str):
    """1장(집필 완료)·2장(미집필) 상태를 만든다 — _previous_chapter_summary 테스트 공용."""
    from app.gateways.dto import ChapterDraftCreateData, SessionCreateData
    from app.gateways.dto import ChapterDraftWriteResult
    from app.models.enums import DraftStatus, SessionType
    from app.schemas.user import UserCreate
    from app.services import user_service

    user = await user_service.create_user(
        gateways, UserCreate(email="recap@example.com", name="테스터", password="test-password-123")
    )
    await gateways.sessions.create(
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
        ChapterDraftWriteResult(
            source_event_ids=[],
            chapter_synopsis=first_chapter_synopsis,
            content="가" * 2000,
            factcheck_report={"flags": []},
            groundedness_report={"flags": []},
            status=DraftStatus.DRAFT,
        ),
    )
    await gateways.commit()
    return autobiography


@pytest.mark.asyncio
async def test_previous_chapter_summary_prefers_stored_synopsis_without_llm_call() -> None:
    """직전 챕터에 시놉시스가 저장돼 있으면 LLM 요약 호출 없이 그걸 그대로 써야
    한다 — 직전 챕터 '완성 본문' 의존을 없애 전 챕터 병렬 집필을 가능하게 하는
    계약(2026-07-17). 시놉시스는 select_toc_candidate가 목차 확정 시점에 미리
    생성해 둔다."""
    from app.gateways.factory import _build_mock_gateways

    synopsis = "1장은 유년기의 가난과 희망을 다룬다."
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs):
        call_count["n"] += 1
        return _FakeCompletion("불려서는 안 되는 응답")

    async def _fake_admin_create_user(*, email, password, user_metadata):
        return uuid.uuid4()

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        autobiography = await _seed_two_chapters(gateways, first_chapter_synopsis=synopsis)

        summary = await autobiography_service._previous_chapter_summary(gateways, autobiography.id, 2)

    assert summary == synopsis
    assert call_count["n"] == 0  # 시놉시스가 있으면 LLM 호출이 없어야 한다


@pytest.mark.asyncio
async def test_previous_chapter_summary_falls_back_to_recap_prompt_when_no_synopsis() -> None:
    """시놉시스가 없는 구버전 초안은 기존 방식(본문을 CHAPTER_RECAP_SYSTEM_PROMPT로
    요약)으로 폴백해야 한다 — 단순 절단(content[-1000:])이 아니라."""
    from app.gateways.factory import _build_mock_gateways

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
        autobiography = await _seed_two_chapters(gateways, first_chapter_synopsis="")

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


# --------------------------------------------------------------------------- #
# Part(대분류) 구조 — 스키마, _toc_text 렌더링, get_chapter_part_context/       #
# get_ordered_parts 헬퍼, episodic 예외                                        #
# --------------------------------------------------------------------------- #


def _make_two_part_toc() -> dict:
    return {
        "narrative_arc": "가난한 유년기에서 자수성가한 노년기까지.",
        "parts": [
            {"part_index": 1, "part_title": "결핍의 시절", "part_arc": "가난과 결핍을 겪는다."},
            {"part_index": 2, "part_title": "도약의 시절", "part_arc": "성공을 향해 나아간다."},
        ],
        "chapters": [
            {
                "chapter_index": 1,
                "title": "1장",
                "theme_keywords": [],
                "connecting_thread": "결핍이 시작된다.",
                "part_index": 1,
            },
            {
                "chapter_index": 2,
                "title": "2장",
                "theme_keywords": [],
                "connecting_thread": "결핍이 절정에 이른다.",
                "part_index": 1,
            },
            {
                "chapter_index": 3,
                "title": "3장",
                "theme_keywords": [],
                "connecting_thread": "도약이 시작된다.",
                "part_index": 2,
            },
            {
                "chapter_index": 4,
                "title": "4장",
                "theme_keywords": [],
                "connecting_thread": "성취를 회수한다.",
                "part_index": 2,
            },
        ],
    }


def test_toc_generation_schema_requires_parts_and_chapter_part_index() -> None:
    candidate_schema = prompts.TOC_GENERATION_SCHEMA["properties"]["candidates"]["items"]
    assert "parts" in candidate_schema["required"]

    part_schema = candidate_schema["properties"]["parts"]["items"]
    assert set(part_schema["required"]) == {"part_index", "part_title", "part_arc"}

    chapter_schema = candidate_schema["properties"]["chapters"]["items"]
    assert "part_index" in chapter_schema["required"]


def test_toc_text_renders_part_headers_in_chapter_order() -> None:
    text = autobiography_service._toc_text(_make_two_part_toc())
    assert "[1부. 결핍의 시절]" in text
    assert "[2부. 도약의 시절]" in text
    assert text.index("[1부. 결핍의 시절]") < text.index("1. 1장")
    assert text.index("1. 1장") < text.index("[2부. 도약의 시절]")
    assert text.index("[2부. 도약의 시절]") < text.index("3. 3장")


def test_toc_text_falls_back_to_flat_rendering_when_parts_absent_or_single() -> None:
    legacy = {"chapters": [{"chapter_index": 1, "title": "1장", "theme_keywords": []}]}
    text = autobiography_service._toc_text(legacy)
    assert "1. 1장" in text
    assert "부." not in text

    single_part = {
        "chapters": [{"chapter_index": 1, "title": "1장", "theme_keywords": [], "part_index": 1}],
        "parts": [{"part_index": 1, "part_title": "전체", "part_arc": "..."}],
    }
    text = autobiography_service._toc_text(single_part)
    assert "1. 1장" in text
    assert "부." not in text  # Part가 1개면 episodic 예외 — 헤더 없이 평평하게 렌더링.


def test_get_chapter_part_context_identifies_opening_and_closing_chapters() -> None:
    autobiography = _make_autobiography(
        toc_data={"selected_candidate_index": 0, "candidates": [_make_two_part_toc()]}
    )

    ch1 = autobiography_service.get_chapter_part_context(autobiography, 1)  # Part 1의 첫 챕터
    assert ch1["is_part_opening"] is True
    assert ch1["is_part_closing"] is False
    assert ch1["prev_part_title"] is None
    assert ch1["next_part_title"] == "도약의 시절"

    ch2 = autobiography_service.get_chapter_part_context(autobiography, 2)  # Part 1의 마지막 챕터
    assert ch2["is_part_opening"] is False
    assert ch2["is_part_closing"] is True

    ch3 = autobiography_service.get_chapter_part_context(autobiography, 3)  # Part 2의 첫 챕터
    assert ch3["is_part_opening"] is True
    assert ch3["prev_part_title"] == "결핍의 시절"
    assert ch3["next_part_title"] is None

    ch4 = autobiography_service.get_chapter_part_context(autobiography, 4)  # Part 2의 마지막 챕터
    assert ch4["is_part_closing"] is True


def test_get_chapter_part_context_returns_none_when_no_real_part_structure() -> None:
    assert autobiography_service.get_chapter_part_context(_make_autobiography(toc_data=None), 1) is None
    assert (
        autobiography_service.get_chapter_part_context(
            _make_autobiography(toc_data={"candidates": [], "selected_candidate_index": None}), 1
        )
        is None
    )

    single_part_toc = {
        "chapters": [{"chapter_index": 1, "title": "1장", "part_index": 1}],
        "parts": [{"part_index": 1, "part_title": "전체", "part_arc": "..."}],
    }
    autobiography = _make_autobiography(toc_data={"selected_candidate_index": 0, "candidates": [single_part_toc]})
    assert autobiography_service.get_chapter_part_context(autobiography, 1) is None

    autobiography = _make_autobiography(
        toc_data={"selected_candidate_index": 0, "candidates": [_make_two_part_toc()]}
    )
    assert autobiography_service.get_chapter_part_context(autobiography, 99) is None


def test_get_ordered_parts_empty_for_zero_or_one_part() -> None:
    assert autobiography_service.get_ordered_parts(_make_autobiography(toc_data=None)) == []

    single_part_toc = {
        "chapters": [{"chapter_index": 1, "title": "1장", "part_index": 1}],
        "parts": [{"part_index": 1, "part_title": "전체", "part_arc": "..."}],
    }
    autobiography = _make_autobiography(toc_data={"selected_candidate_index": 0, "candidates": [single_part_toc]})
    assert autobiography_service.get_ordered_parts(autobiography) == []


def test_get_ordered_parts_returns_sorted_parts() -> None:
    toc = _make_two_part_toc()
    toc["parts"] = list(reversed(toc["parts"]))  # 저장 순서가 뒤바뀌어도 정렬돼 나와야 한다.
    autobiography = _make_autobiography(toc_data={"selected_candidate_index": 0, "candidates": [toc]})
    parts = autobiography_service.get_ordered_parts(autobiography)
    assert [p["part_index"] for p in parts] == [1, 2]


def test_customized_toc_prompt_forces_single_part_for_episodic_structure() -> None:
    messages = prompts.build_customized_toc_prompt(
        event_summaries_with_scores="사건 요약", structure_key="episodic"
    )
    system_content = messages[0]["content"]
    assert "정확히 1개의 Part" in system_content


def test_customized_toc_prompt_injects_part_shaping_hint_for_non_episodic_structure() -> None:
    messages = prompts.build_customized_toc_prompt(
        event_summaries_with_scores="사건 요약", structure_key="chronological"
    )
    system_content = messages[0]["content"]
    assert prompts._PART_SHAPING_HINTS["chronological"] in system_content


def test_part_marker_pattern_strips_marker_lines() -> None:
    text = "머리말.\n\n=== PART 2: 도약의 시절 ===\n\n본문 이어짐."
    cleaned = autobiography_service._PART_MARKER_PATTERN.sub("", text)
    assert "=== PART" not in cleaned
    assert "머리말." in cleaned
    assert "본문 이어짐." in cleaned


@pytest.mark.asyncio
async def test_select_toc_candidate_generates_and_persists_part_synopses() -> None:
    from app.gateways.factory import _build_mock_gateways
    from app.schemas.user import UserCreate
    from app.services import user_service

    async def _fake_admin_create_user(*, email, password, user_metadata):
        return uuid.uuid4()

    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeCompletion("책 전체 시놉시스")
        return _FakeCompletion(f"Part 시놉시스 {call_count['n']}")

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        assert schema_name == "book_title"
        return {"title": "책 제목"}

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        # select_toc_candidate가 이제 챕터 시놉시스 사전 생성을 위해 챕터별 이벤트
        # 검색(임베딩 호출)도 수행한다(2026-07-17).
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        user = await user_service.create_user(
            gateways, UserCreate(email="parts@example.com", name="테스터", password="test-password-123")
        )
        autobiography = await gateways.autobiographies.create(user.id)
        toc = _make_two_part_toc()
        await gateways.autobiographies.update(
            autobiography.id,
            toc_data={"generated_at": "now", "candidates": [toc], "selected_candidate_index": None},
        )
        await gateways.commit()

        result = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)

    saved_parts = result.toc_data["candidates"][0]["parts"]
    assert saved_parts[0]["part_synopsis"] == "Part 시놉시스 2"
    assert saved_parts[1]["part_synopsis"] == "Part 시놉시스 3"


# --------------------------------------------------------------------------- #
# Part 시놉시스 근거 확보 — 2026-07-17 실사용 중 발견된 환각("옥스퍼드에서       #
# 태어났다"는 근거가 "도서관에서 태어났다"로 구체화되어 지어내진 사고)의 수정.   #
# part_synopsis가 실제 사건 자료 없이 만들어지던 게 원인이었으므로, 이제       #
# select_toc_candidate가 이미 검색해 둔 챕터별 사건을 Part 시놉시스 프롬프트에  #
# 실제 근거로 전달하는지 확인한다.                                             #
# --------------------------------------------------------------------------- #


def test_part_synopsis_prompt_includes_event_summaries_as_grounding() -> None:
    messages = prompts.build_part_synopsis_prompt(
        book_synopsis="책 시놉시스",
        part_title="1부",
        part_arc_seed="씨앗",
        chapter_titles=["1장"],
        event_summaries=["부산에서 태어남", "학교에 입학함"],
    )
    user_content = messages[1]["content"]
    assert "부산에서 태어남" in user_content
    assert "학교에 입학함" in user_content


def test_part_synopsis_prompt_falls_back_to_abstract_notice_when_no_events() -> None:
    """근거 사건이 하나도 없으면(빈 검색 결과 등) 구체적 장면을 지어내지 말라는
    안내로 폴백해야 한다 — 빈 [근거 사건 요약] 블록만 두면 모델이 오히려 자유
    창작으로 채워 넣을 위험이 있다."""
    messages = prompts.build_part_synopsis_prompt(
        book_synopsis="책 시놉시스",
        part_title="1부",
        part_arc_seed="씨앗",
        chapter_titles=["1장"],
        event_summaries=[],
    )
    user_content = messages[1]["content"]
    assert "근거 사건 없음" in user_content


class _FakeEventForPartSynopsis:
    def __init__(self, event_id: uuid.UUID, one_line_summary: str) -> None:
        self.id = event_id
        self.one_line_summary = one_line_summary


@pytest.mark.asyncio
async def test_generate_part_synopses_groups_events_by_part_and_dedupes() -> None:
    """같은 사건이 한 Part 안의 여러 챕터에서 검색돼도 그 Part의 근거에는 한 번만
    들어가야 하고(event.id 기준 중복 제거), 다른 Part의 근거가 섞여 들어가면
    안 된다 — Part별로 정확히 분리된 근거만 봐야 그 Part 시놉시스가 실제로
    그 Part의 사건에 기반해 만들어진다."""
    chosen = _make_two_part_toc()  # 1,2장 = Part 1 / 3,4장 = Part 2
    event_a = _FakeEventForPartSynopsis(uuid.uuid4(), "사건 A")
    event_b = _FakeEventForPartSynopsis(uuid.uuid4(), "사건 B")
    event_c = _FakeEventForPartSynopsis(uuid.uuid4(), "사건 C")
    # chosen["chapters"]와 같은 순서(1,2,3,4장). 1장·2장(둘 다 Part 1)이 event_a를
    # 공유하도록 해 중복 제거가 실제로 동작하는지 확인한다.
    chapter_events = [
        [event_a],
        [event_a, event_b],
        [event_c],
        [],
    ]

    captured_prompts: list[list[dict]] = []

    async def _fake_chat_completion(messages, **kwargs):
        captured_prompts.append(messages)
        return _FakeCompletion("생성된 시놉시스")

    with patch("app.clients.solar.chat_completion", new=_fake_chat_completion):
        result = await autobiography_service._generate_part_synopses(
            "책 시놉시스", chosen, chapter_events
        )

    assert result[1] == "생성된 시놉시스"
    part1_prompt_content = captured_prompts[0][1]["content"]
    assert "사건 A" in part1_prompt_content
    assert "사건 B" in part1_prompt_content
    assert part1_prompt_content.count("사건 A") == 1  # 중복 제거 확인

    part2_prompt_content = captured_prompts[1][1]["content"]
    assert "사건 C" in part2_prompt_content
    assert "사건 A" not in part2_prompt_content  # 다른 Part의 근거가 섞이면 안 됨


# --------------------------------------------------------------------------- #
# _normalize_toc_parts — Part 최소 챕터 수(3개) 강제 병합 + "N부"/"Part N"     #
# 중복 표기 방지. 2026-07-17 실사용 중 프롬프트 지시만으로는 LLM이 이 두      #
# 제약을 어기는 사례(Part 5가 챕터 1개, "1부. Part 1: ..." 중복 표기)가       #
# 확인돼 결정론적 후처리로 보강했다.                                          #
# --------------------------------------------------------------------------- #


def _chapter(index: int, part_index: int) -> dict:
    return {
        "chapter_index": index,
        "title": f"{index}장",
        "theme_keywords": [],
        "connecting_thread": "",
        "part_index": part_index,
    }


@pytest.mark.parametrize(
    "raw_title,expected",
    [
        ("Part 1: Childhood Curiosity", "Childhood Curiosity"),
        ("1부: 유년기의 기억", "유년기의 기억"),
        ("제1부 유년기", "유년기"),
        ("유년기", "유년기"),  # 접두어가 없으면 그대로.
    ],
)
def test_strip_part_title_prefix_handles_various_formats(raw_title: str, expected: str) -> None:
    assert autobiography_service._strip_part_title_prefix(raw_title) == expected


def test_normalize_toc_parts_strips_redundant_part_number_prefix() -> None:
    candidate = {
        "narrative_arc": "...",
        "parts": [{"part_index": 1, "part_title": "Part 1: Childhood Curiosity", "part_arc": "..."}],
        "chapters": [_chapter(1, 1), _chapter(2, 1), _chapter(3, 1)],
    }
    result = autobiography_service._normalize_toc_parts(candidate)
    assert result["parts"][0]["part_title"] == "Childhood Curiosity"


def test_normalize_toc_parts_merges_undersized_first_part_into_next() -> None:
    candidate = {
        "narrative_arc": "...",
        "parts": [
            {"part_index": 1, "part_title": "짧은 시작", "part_arc": "..."},
            {"part_index": 2, "part_title": "본편", "part_arc": "..."},
        ],
        "chapters": [_chapter(1, 1), _chapter(2, 2), _chapter(3, 2), _chapter(4, 2)],
    }
    result = autobiography_service._normalize_toc_parts(candidate)

    assert len(result["parts"]) == 1
    assert result["parts"][0]["part_index"] == 1
    assert result["parts"][0]["part_title"] == "본편"
    assert all(c["part_index"] == 1 for c in result["chapters"])


def test_normalize_toc_parts_merges_undersized_middle_part_into_previous() -> None:
    candidate = {
        "narrative_arc": "...",
        "parts": [
            {"part_index": 1, "part_title": "1부", "part_arc": "..."},
            {"part_index": 2, "part_title": "2부", "part_arc": "..."},
            {"part_index": 3, "part_title": "3부", "part_arc": "..."},
        ],
        "chapters": [
            _chapter(1, 1), _chapter(2, 1), _chapter(3, 1),
            _chapter(4, 2),  # Part 2는 챕터 1개뿐 — 병합 대상.
            _chapter(5, 3), _chapter(6, 3), _chapter(7, 3),
        ],
    }
    result = autobiography_service._normalize_toc_parts(candidate)

    assert [p["part_index"] for p in result["parts"]] == [1, 2]
    assert result["parts"][0]["part_title"] == "1부"
    assert result["parts"][1]["part_title"] == "3부"  # 2부가 사라지고 3부가 새 2번이 됨.
    ch4 = next(c for c in result["chapters"] if c["chapter_index"] == 4)
    assert ch4["part_index"] == 1  # 4장(원래 2부)이 1부로 흡수됨.


def test_normalize_toc_parts_does_not_merge_when_only_one_part() -> None:
    candidate = {
        "narrative_arc": "...",
        "parts": [{"part_index": 1, "part_title": "Part 1: 전체", "part_arc": "..."}],
        "chapters": [_chapter(1, 1)],
    }
    result = autobiography_service._normalize_toc_parts(candidate)
    assert len(result["parts"]) == 1
    assert result["parts"][0]["part_title"] == "전체"  # 접두어는 제거되지만 병합 로직은 스킵.


def test_normalize_toc_parts_cascades_without_error_when_too_few_chapters() -> None:
    """극단 케이스: Part 3개 모두 챕터가 1개씩뿐이면 연쇄 병합돼 결국 Part 1개로
    수렴해야 하고, 이 과정에서 에러가 나면 안 된다."""
    candidate = {
        "narrative_arc": "...",
        "parts": [
            {"part_index": 1, "part_title": "1부", "part_arc": "..."},
            {"part_index": 2, "part_title": "2부", "part_arc": "..."},
            {"part_index": 3, "part_title": "3부", "part_arc": "..."},
        ],
        "chapters": [_chapter(1, 1), _chapter(2, 2), _chapter(3, 3)],
    }
    result = autobiography_service._normalize_toc_parts(candidate)

    assert len(result["parts"]) == 1
    assert len(result["chapters"]) == 3
    assert all(c["part_index"] == 1 for c in result["chapters"])


@pytest.mark.asyncio
async def test_write_chapter_injects_part_context_and_reuses_synopsis_on_repair() -> None:
    """수리(팩트체크/근거검증 flag로 촉발)가 걸려도 챕터 시놉시스는 한 번만
    생성돼야 한다 — Part 컨텍스트 주입이 수리/채택 로직을 건드리지 않는지
    확인하는 회귀 테스트. 이 테스트는 시놉시스 없는 초안(구버전 경로)을 직접
    만들므로 write_chapter의 즉석 시놉시스 생성 폴백도 함께 검증한다."""
    from app.gateways.dto import ChapterDraftCreateData, EventCreateData, SessionCreateData
    from app.gateways.factory import _build_mock_gateways
    from app.models.enums import EventSourceType, SessionType
    from app.schemas.user import UserCreate
    from app.services import user_service

    async def _fake_admin_create_user(*, email, password, user_metadata):
        return uuid.uuid4()

    _BAD = "지어낸 문장."
    _GOOD = "나는 부산에서 태어났다."
    captured_synopsis_messages: list[list[dict]] = []
    call_count = {"n": 0}

    async def _fake_chat_completion(messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            captured_synopsis_messages.append(messages)
            return _FakeCompletion("챕터 시놉시스")
        if call_count["n"] == 2:
            return _FakeCompletion(_BAD)
        return _FakeCompletion(_GOOD)

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        if schema_name == "groundedness_judge":
            user_content = messages[1]["content"]
            if _BAD in user_content:
                return {"flags": [{"sentence": _BAD, "reason": "근거 없음"}]}
            return {"flags": []}
        if schema_name == "fact_reextraction":
            return {"facts": []}
        if schema_name == "ner_extraction":
            return {"people": []}
        raise AssertionError(f"unexpected schema {schema_name}")

    async def _fake_groundedness_api_check(*, context: str, answer: str) -> str:
        return "notGrounded"  # 2차 게이트가 판정자 플래그를 철회하지 않도록 고정

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.groundedness.check", new=_fake_groundedness_api_check),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        user = await user_service.create_user(
            gateways, UserCreate(email="partchapter@example.com", name="테스터", password="test-password-123")
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

        autobiography = await gateways.autobiographies.create(user.id)
        toc = _make_two_part_toc()
        await gateways.chapters.replace_all(
            autobiography.id,
            [
                ChapterDraftCreateData(chapter_index=c["chapter_index"], title=c["title"])
                for c in toc["chapters"]
            ],
        )
        await gateways.autobiographies.update(
            autobiography.id,
            toc_data={"generated_at": "now", "candidates": [toc], "selected_candidate_index": 0},
            book_synopsis="책 전체 시놉시스",
        )
        await gateways.commit()

        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
        chapter1 = next(c for c in chapters if c.chapter_index == 1)

        result = await autobiography_service.write_chapter(gateways, chapter1.id)

    synopsis_user_content = captured_synopsis_messages[0][1]["content"]
    assert "결핍의 시절" in synopsis_user_content
    assert "이 챕터는 이 Part의 첫 챕터입니다." in synopsis_user_content

    assert result.content == _GOOD
    assert len(captured_synopsis_messages) == 1  # 수리 중에도 시놉시스는 재생성되지 않음.
    assert call_count["n"] == 3  # 시놉시스 1회 + 집필 1회 + 수리 1회


@pytest.mark.asyncio
async def test_finalize_manuscript_inserts_part_markers_and_strips_leftover_marker() -> None:
    from app.gateways.dto import ChapterDraftCreateData, ChapterDraftWriteResult
    from app.gateways.factory import _build_mock_gateways
    from app.models.enums import DraftStatus
    from app.schemas.user import UserCreate
    from app.services import user_service

    async def _fake_admin_create_user(*, email, password, user_metadata):
        return uuid.uuid4()

    captured: dict = {}

    async def _fake_chat_completion(messages, **kwargs):
        captured["full_manuscript"] = messages[1]["content"]
        # 지시를 어기고 마커를 하나 남긴 응답을 흉내낸다 — 방어적 제거가 실제로 동작하는지 확인.
        return _FakeCompletion("=== PART 1: 결핍의 시절 ===\n\n윤문된 원고 본문.")

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        user = await user_service.create_user(
            gateways, UserCreate(email="finalize-parts@example.com", name="테스터", password="test-password-123")
        )
        autobiography = await gateways.autobiographies.create(user.id)
        toc = _make_two_part_toc()
        await gateways.chapters.replace_all(
            autobiography.id,
            [
                ChapterDraftCreateData(chapter_index=c["chapter_index"], title=c["title"])
                for c in toc["chapters"]
            ],
        )
        await gateways.autobiographies.update(
            autobiography.id,
            toc_data={"generated_at": "now", "candidates": [toc], "selected_candidate_index": 0},
        )
        chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
        for chapter in chapters:
            await gateways.chapters.save_write_result(
                chapter.id,
                ChapterDraftWriteResult(
                    source_event_ids=[],
                    chapter_synopsis="시놉시스",
                    content=f"{chapter.chapter_index}장 본문.",
                    factcheck_report={"flags": []},
                    groundedness_report={"flags": []},
                    status=DraftStatus.REVIEWED,
                ),
            )
        await gateways.commit()

        result = await autobiography_service.finalize_manuscript(gateways, autobiography.id)

    full_manuscript = captured["full_manuscript"]
    assert full_manuscript.count("=== PART") == 2
    assert full_manuscript.index("=== PART 1: 결핍의 시절 ===") < full_manuscript.index("[1장.")
    assert full_manuscript.index("[2장.") < full_manuscript.index("=== PART 2: 도약의 시절 ===")
    assert full_manuscript.index("=== PART 2: 도약의 시절 ===") < full_manuscript.index("[3장.")

    assert "=== PART" not in result.final_content
    assert "윤문된 원고 본문." in result.final_content
