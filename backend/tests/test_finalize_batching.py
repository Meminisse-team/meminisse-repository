"""finalize_manuscript Part 단위 배치 분할 회귀 테스트(2026-07-19).

배경: 챕터 19개(53,692자 실측)를 한 번의 통일성 윤문 호출에 몰아넣다가 API
타임아웃(90초)으로 실패하는 사고가 실사용 중 재현됐다. Part 경계를 배치
경계로 삼아 여러 번의 작은 호출로 나누고, 배치별 타임아웃도 넉넉히 늘렸다
(app/clients/solar.py의 timeout 파라미터). 이 파일은 (1) 배치 나누기 규칙
자체(Part 경계 존중, 크기 상한, Part 없는 책 폴백), (2) 배치 하나가 실패해도
나머지·최종본 조립이 안전한지, (3) solar.chat_completion이 timeout을 실제로
전달하는지를 검증한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.gateways.dto import ChapterDraftRecord
from app.models.enums import DraftStatus
from app.services.autobiography_service import (
    _FINALIZE_BATCH_MAX_CHAPTERS,
    _finalize_batch,
    _group_chapters_for_finalize,
)


def _make_chapter(index: int, *, content: str = "본문") -> ChapterDraftRecord:
    return ChapterDraftRecord(
        id=uuid.uuid4(),
        autobiography_id=uuid.uuid4(),
        chapter_index=index,
        title=f"{index}장",
        chapter_synopsis=None,
        content=content,
        source_event_ids=[],
        factcheck_report=None,
        groundedness_report=None,
        status=DraftStatus.REVIEWED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _autobiography_with_parts(*, parts: list[dict], chapter_to_part: dict[int, int]) -> SimpleNamespace:
    chapters_toc = [
        {"chapter_index": idx, "title": f"{idx}장", "part_index": part_idx}
        for idx, part_idx in chapter_to_part.items()
    ]
    return SimpleNamespace(
        toc_data={
            "selected_candidate_index": 0,
            "candidates": [{"parts": parts, "chapters": chapters_toc}],
        }
    )


# ---------------------------------------------------------------------------
# _group_chapters_for_finalize
# ---------------------------------------------------------------------------


def test_grouping_never_mixes_two_parts_in_one_batch() -> None:
    autobiography = _autobiography_with_parts(
        parts=[
            {"part_index": 1, "part_title": "결핍의 시절", "part_arc": ""},
            {"part_index": 2, "part_title": "도약의 시절", "part_arc": ""},
        ],
        chapter_to_part={1: 1, 2: 1, 3: 2, 4: 2},
    )
    chapters = [_make_chapter(i) for i in range(1, 5)]

    batches = _group_chapters_for_finalize(autobiography, chapters)

    assert [c.chapter_index for c in batches[0]] == [1, 2]
    assert [c.chapter_index for c in batches[1]] == [3, 4]


def test_grouping_splits_a_part_larger_than_the_batch_cap() -> None:
    """실측 사례(Part 하나가 챕터 10개)를 재현 — 상한을 넘는 Part는 그 안에서
    순서대로 더 쪼개져야 한다."""
    part_size = _FINALIZE_BATCH_MAX_CHAPTERS + 3
    autobiography = _autobiography_with_parts(
        parts=[{"part_index": 1, "part_title": "긴 Part", "part_arc": ""}],
        chapter_to_part={i: 1 for i in range(1, part_size + 1)},
    )
    chapters = [_make_chapter(i) for i in range(1, part_size + 1)]

    batches = _group_chapters_for_finalize(autobiography, chapters)

    assert all(len(batch) <= _FINALIZE_BATCH_MAX_CHAPTERS for batch in batches)
    # 순서를 보존하며 정확히 전부 배정돼야 한다(누락·중복 없음).
    flattened = [c.chapter_index for batch in batches for c in batch]
    assert flattened == list(range(1, part_size + 1))


def test_grouping_falls_back_to_flat_chunks_without_part_structure() -> None:
    """Part 구조가 아예 없는 책(get_chapter_part_context가 전부 None)도 같은
    상한으로 안전하게 나뉘어야 한다."""
    autobiography = SimpleNamespace(toc_data=None)
    chapter_count = _FINALIZE_BATCH_MAX_CHAPTERS * 2 + 1
    chapters = [_make_chapter(i) for i in range(1, chapter_count + 1)]

    batches = _group_chapters_for_finalize(autobiography, chapters)

    assert all(len(batch) <= _FINALIZE_BATCH_MAX_CHAPTERS for batch in batches)
    flattened = [c.chapter_index for batch in batches for c in batch]
    assert flattened == list(range(1, chapter_count + 1))


# ---------------------------------------------------------------------------
# _finalize_batch — 실패 격리
# ---------------------------------------------------------------------------


class _FakeChaptersGateway:
    def __init__(self) -> None:
        self.updated: dict[uuid.UUID, str] = {}

    async def update_content(self, chapter_id, content) -> None:
        self.updated[chapter_id] = content


class _FakeGateways:
    def __init__(self) -> None:
        self.chapters = _FakeChaptersGateway()


@pytest.mark.asyncio
async def test_finalize_batch_keeps_original_content_when_call_raises() -> None:
    """API 타임아웃 등으로 배치 호출 자체가 예외를 던지면, 그 배치의 챕터는
    원본을 유지하고 예외가 상위로 전파되지 않아야 한다(다른 배치를 막지
    않기 위함)."""
    autobiography = _autobiography_with_parts(parts=[], chapter_to_part={})
    batch = [_make_chapter(1, content="원본 1장"), _make_chapter(2, content="원본 2장")]
    gateways = _FakeGateways()

    async def _raise(*args, **kwargs):
        raise TimeoutError("API timeout")

    with patch("app.clients.solar.chat_completion", new=_raise):
        await _finalize_batch(
            gateways,
            autobiography=autobiography,
            batch=batch,
            style_bible_text="",
            confirmed=None,
        )

    assert gateways.chapters.updated == {}  # 아무 챕터도 덮어써지지 않았다


@pytest.mark.asyncio
async def test_finalize_batch_keeps_original_content_when_markers_mismatch() -> None:
    """응답에 챕터 마커가 어긋나 있으면(모델이 지시를 어김) 파싱 실패로 보고
    원본을 유지해야 한다."""
    autobiography = _autobiography_with_parts(parts=[], chapter_to_part={})
    batch = [_make_chapter(1, content="원본 1장")]
    gateways = _FakeGateways()

    class _FakeCompletion:
        choices = [SimpleNamespace(message=SimpleNamespace(content="마커 없는 응답"))]

    async def _fake(*args, **kwargs):
        return _FakeCompletion()

    with patch("app.clients.solar.chat_completion", new=_fake):
        await _finalize_batch(
            gateways,
            autobiography=autobiography,
            batch=batch,
            style_bible_text="",
            confirmed=None,
        )

    assert gateways.chapters.updated == {}


@pytest.mark.asyncio
async def test_finalize_batch_updates_chapters_on_success() -> None:
    autobiography = _autobiography_with_parts(parts=[], chapter_to_part={})
    ch1 = _make_chapter(1, content="원본 1장")
    gateways = _FakeGateways()

    class _FakeCompletion:
        choices = [SimpleNamespace(message=SimpleNamespace(content="<<<CHAPTER 1>>>\n윤문된 1장."))]

    async def _fake(*args, **kwargs):
        return _FakeCompletion()

    with patch("app.clients.solar.chat_completion", new=_fake):
        await _finalize_batch(
            gateways,
            autobiography=autobiography,
            batch=[ch1],
            style_bible_text="",
            confirmed=None,
        )

    assert gateways.chapters.updated[ch1.id] == "윤문된 1장."


@pytest.mark.asyncio
async def test_finalize_batch_passes_generous_timeout_to_solar() -> None:
    """배치 호출은 전역 기본(90초)보다 넉넉한 타임아웃을 개별 지정해야 한다."""
    autobiography = _autobiography_with_parts(parts=[], chapter_to_part={})
    ch1 = _make_chapter(1, content="원본 1장")
    gateways = _FakeGateways()
    captured_kwargs: dict = {}

    class _FakeCompletion:
        choices = [SimpleNamespace(message=SimpleNamespace(content="<<<CHAPTER 1>>>\n윤문된 1장."))]

    async def _fake(messages, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeCompletion()

    with patch("app.clients.solar.chat_completion", new=_fake):
        await _finalize_batch(
            gateways,
            autobiography=autobiography,
            batch=[ch1],
            style_bible_text="",
            confirmed=None,
        )

    assert captured_kwargs.get("timeout", 0) > 90


# ---------------------------------------------------------------------------
# solar.chat_completion의 timeout 파라미터 전달
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solar_chat_completion_forwards_timeout_to_client() -> None:
    from app.clients import solar

    captured_kwargs: dict = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

    with patch("app.clients.solar.get_upstage_client", return_value=fake_client):
        await solar.chat_completion([{"role": "user", "content": "hi"}], timeout=180.0)

    assert captured_kwargs["timeout"] == 180.0


@pytest.mark.asyncio
async def test_solar_chat_completion_omits_timeout_when_not_given() -> None:
    """timeout을 안 넘기면 클라이언트 기본값(90초)이 그대로 적용되도록
    kwargs에 아예 넣지 않아야 한다."""
    from app.clients import solar

    captured_kwargs: dict = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

    with patch("app.clients.solar.get_upstage_client", return_value=fake_client):
        await solar.chat_completion([{"role": "user", "content": "hi"}])

    assert "timeout" not in captured_kwargs
