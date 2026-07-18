"""문학적 완성도 개선(2026-07-18) 회귀 테스트.

호킹 테스트 계정의 실제 생성 결과에서 확인된 체계적 결함들 — 같은 사건이 여러
챕터에서 반복 서술(크로스챕터 중복), 한 챕터가 생애 전체를 흡수(스코프 폭주),
분량 미달(목표의 40~60%), 교열 결함(오탈자·시점 붕괴) — 을 막기 위해 도입한
메커니즘들을 검증한다:

1. _assign_events_to_chapters: 이벤트→챕터 배타적 배정(결정론적).
2. select_toc_candidate가 배정 결과를 ChapterDraft.source_event_ids로 저장하고,
   write_chapter가 재검색 없이 그대로 사용.
3. 분량 확장 패스: 초안이 3,000자 미만이면 정교화 전용 확장 1회.
4. 검수(교열) 패스: 길이가 ±30% 넘게 변하면(교열이 아니라 개고) 원본 유지.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.gateways.dto import EventCreateData, EventRecord, SessionCreateData
from app.gateways.factory import Gateways, _build_mock_gateways
from app.models.enums import EventSourceType, SessionType
from app.schemas.user import UserCreate
from app.services import autobiography_service, user_service
from app.services.autobiography_service import (
    _assign_events_to_chapters,
    _chapter_time_scope,
    _other_chapter_titles,
)


def _make_event(occurred_at_label: str | None = None) -> EventRecord:
    return EventRecord(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source_type=EventSourceType.SESSION_CHAT,
        session_id=None,
        media_asset_id=None,
        source_span=None,
        life_period=None,
        occurred_at_label=occurred_at_label,
        place=None,
        people=None,
        one_line_summary="요약",
        prose_paragraph="문단",
        emotion_tag=None,
        emotion_intensity=None,
        emotion_inferred=False,
        labels={},
        confidence=None,
        verified=True,
        is_must_include=False,
        embedding=None,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# _assign_events_to_chapters
# ---------------------------------------------------------------------------


def test_assign_events_duplicate_goes_to_higher_rank_chapter() -> None:
    shared = _make_event()
    only_a = _make_event()
    # 챕터 A에서는 shared가 2순위, 챕터 B에서는 1순위 → B가 가져간다.
    assigned = _assign_events_to_chapters([[only_a, shared], [shared]])
    assert [e.id for e in assigned[0]] == [only_a.id]
    assert [e.id for e in assigned[1]] == [shared.id]


def test_assign_events_tie_goes_to_earlier_chapter() -> None:
    shared = _make_event()
    filler = _make_event()
    # 두 챕터 모두 1순위(동순위) → 앞 챕터가 가져간다.
    assigned = _assign_events_to_chapters([[shared], [shared, filler]])
    assert [e.id for e in assigned[0]] == [shared.id]
    assert [e.id for e in assigned[1]] == [filler.id]


def test_assign_events_is_deterministic() -> None:
    events = [_make_event() for _ in range(5)]
    chapter_events = [[events[0], events[1], events[2]], [events[1], events[3]], [events[2], events[4]]]
    first = _assign_events_to_chapters(chapter_events)
    second = _assign_events_to_chapters(chapter_events)
    assert [[e.id for e in ch] for ch in first] == [[e.id for e in ch] for ch in second]


def test_assign_events_empty_chapter_falls_back_to_top_of_original() -> None:
    a, b, c, d = (_make_event() for _ in range(4))
    # 뒤 챕터의 사건 전부가 앞 챕터에 더 높은 순위로 배정됨 → 빈 챕터 폴백으로
    # 원래 검색 상위 3개를 남긴다(이때만 중복 허용).
    assigned = _assign_events_to_chapters([[a, b, c, d], [a, b, c, d]])
    assert [e.id for e in assigned[0]] == [a.id, b.id, c.id, d.id]
    assert [e.id for e in assigned[1]] == [a.id, b.id, c.id]


# ---------------------------------------------------------------------------
# _chapter_time_scope / _other_chapter_titles
# ---------------------------------------------------------------------------


def test_chapter_time_scope_dedupes_and_skips_blank_labels() -> None:
    events = [
        _make_event("1963년"),
        _make_event("1963년"),
        _make_event("  "),
        _make_event(None),
        _make_event("1965년 여름"),
    ]
    assert _chapter_time_scope(events) == "1963년, 1965년 여름"


def test_chapter_time_scope_returns_none_without_labels() -> None:
    assert _chapter_time_scope([_make_event(None)]) is None
    assert _chapter_time_scope([]) is None


def test_other_chapter_titles_excludes_self() -> None:
    autobiography = SimpleNamespace(
        toc_data={
            "selected_candidate_index": 0,
            "candidates": [
                {
                    "chapters": [
                        {"chapter_index": 1, "title": "어린 시절"},
                        {"chapter_index": 2, "title": "진단"},
                        {"chapter_index": 3, "title": "결혼"},
                    ]
                }
            ],
        }
    )
    assert _other_chapter_titles(autobiography, 2) == ["1장. 어린 시절", "3장. 결혼"]


def test_other_chapter_titles_empty_without_selected_toc() -> None:
    assert _other_chapter_titles(SimpleNamespace(toc_data=None), 1) == []
    assert _other_chapter_titles(SimpleNamespace(toc_data={"candidates": []}), 1) == []


# ---------------------------------------------------------------------------
# 파이프라인 통합: 배정 저장 → write_chapter 재사용 / 확장·검수 패스
# ---------------------------------------------------------------------------

_STRUCTURED_RESPONSES = {
    "event_merge_judge": {"same_event": False, "reasoning": "다른 사건"},
    "customization_recommendation": {
        "tones": ["confessional"],
        "structures": ["chronological"],
        "concepts": ["family"],
        "reasoning": "테스트",
    },
    "toc_generation": {
        "candidates": [
            {"chapters": [{"chapter_index": 1, "title": "부산의 어린 시절", "theme_keywords": ["부산"]}]},
        ]
    },
    "book_title": {"title": "테스트 자서전"},
    "fact_reextraction": {"facts": []},
    "groundedness_judge": {"flags": []},
    "ner_extraction": {"people": []},  # 등장인물 스캔 생략(이 테스트의 관심사 아님)
}


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    return _STRUCTURED_RESPONSES[schema_name]


async def _fake_admin_create_user(*, email: str, password: str, user_metadata: dict) -> uuid.UUID:
    return uuid.uuid4()


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


def _routing_chat_completion(*, writing: str, expansion: str = "", proofread: str = "", calls: dict | None = None):
    """system 프롬프트 내용으로 집필/확장/교열 호출을 구분해 각기 다른 응답을 주는
    가짜 chat_completion. calls에 단계별 호출 횟수를 기록한다."""

    async def _fake(messages, **kwargs) -> _FakeCompletion:
        system = messages[0]["content"]
        if "목표 분량(4,000~6,000자)에 크게 못 미칩니다" in system:
            if calls is not None:
                calls["expansion"] = calls.get("expansion", 0) + 1
            return _FakeCompletion(expansion)
        if "교열하세요" in system:
            if calls is not None:
                calls["proofread"] = calls.get("proofread", 0) + 1
            return _FakeCompletion(proofread)
        if "챕터 본문을 집필하세요" in system:
            if calls is not None:
                calls["writing"] = calls.get("writing", 0) + 1
            return _FakeCompletion(writing)
        return _FakeCompletion("일반 응답입니다.")

    return _fake


async def _seed_pipeline_until_select(gateways: Gateways):
    """유저·세션·이벤트를 심고 consolidate → 목차 생성 → 목차 선택까지 진행한다."""
    user = await user_service.create_user(
        gateways, UserCreate(email="lit@example.com", name="테스터", password="test-password-123")
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
                one_line_summary="부산 출생", prose_paragraph="나는 부산에서 태어났다.",
                verified=True, emotion_intensity=3, occurred_at_label="1950년",
            ),
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT,
                one_line_summary="학창 시절", prose_paragraph="부산에서 학교를 다녔다.",
                verified=True, emotion_intensity=4, occurred_at_label="1960년대",
            ),
        ]
    )
    await gateways.events.bulk_update_embeddings(
        [(events[0].id, [1.0, 0.0, 0.0]), (events[1].id, [0.0, 1.0, 0.0])]
    )
    await gateways.commit()

    autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)
    autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography.id)
    autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)
    return autobiography


@pytest.mark.asyncio
async def test_select_persists_assignment_and_write_skips_retrieval() -> None:
    """select_toc_candidate가 배정된 이벤트 id를 ChapterDraft에 저장하고,
    write_chapter는 재검색 없이 그 id들을 그대로 써야 한다 — 재검색하면 다른
    챕터에 배정된 사건이 다시 섞여 들어와 배타적 배정이 무의미해진다."""
    long_body = "부산에서 보낸 나날을 기억한다. " * 250 + "[E1]"
    with (
        patch(
            "app.clients.solar.chat_completion",
            new=_routing_chat_completion(writing=long_body, proofread=""),
        ),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        autobiography = await _seed_pipeline_until_select(gateways)

        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
        assert len(chapters) == 1
        assigned_ids = chapters[0].source_event_ids
        assert len(assigned_ids) == 2  # 단일 챕터라 두 사건 모두 이 챕터에 배정

        async def _must_not_retrieve(*args, **kwargs):
            raise AssertionError("write_chapter가 배정 저장분 대신 재검색을 수행했다")

        with patch(
            "app.services.autobiography_service._retrieve_events_for_chapter",
            new=_must_not_retrieve,
        ):
            chapter = await autobiography_service.write_chapter(gateways, chapters[0].id)

        assert chapter.content
        assert sorted(chapter.source_event_ids) == sorted(assigned_ids)


@pytest.mark.asyncio
async def test_short_chapter_triggers_expansion_pass_once() -> None:
    """초안이 3,000자 미만이면 확장 패스가 정확히 1회 돌고, 더 길어진 결과가
    채택돼야 한다."""
    short_body = "짧은 초안이다. [E1]"
    expanded_body = "확장된 본문 문장이다. " * 300 + "[E1]"
    calls: dict = {}
    with (
        patch(
            "app.clients.solar.chat_completion",
            new=_routing_chat_completion(
                writing=short_body, expansion=expanded_body, proofread="", calls=calls
            ),
        ),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        autobiography = await _seed_pipeline_until_select(gateways)
        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)

        chapter = await autobiography_service.write_chapter(gateways, chapters[0].id)

    assert calls.get("expansion") == 1
    assert "확장된 본문" in (chapter.content or "")
    assert "[E1]" not in (chapter.content or "")  # 태그는 저장 전에 회수돼야 한다


@pytest.mark.asyncio
async def test_proofread_result_rejected_when_length_drifts_over_30_percent() -> None:
    """검수(교열) 결과가 원본 대비 ±30% 넘게 길이가 변하면 개고로 보고 원본을
    유지해야 한다."""
    long_body = "부산의 여름을 기억한다. " * 300 + "[E1]"
    runaway_proofread = "완전히 새로 쓴 본문이다. " * 900
    calls: dict = {}
    with (
        patch(
            "app.clients.solar.chat_completion",
            new=_routing_chat_completion(
                writing=long_body, proofread=runaway_proofread, calls=calls
            ),
        ),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        autobiography = await _seed_pipeline_until_select(gateways)
        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)

        chapter = await autobiography_service.write_chapter(gateways, chapters[0].id)

    assert calls.get("proofread") == 1
    assert "부산의 여름" in (chapter.content or "")
    assert "완전히 새로 쓴" not in (chapter.content or "")


@pytest.mark.asyncio
async def test_proofread_result_adopted_within_length_guard() -> None:
    """교열 결과가 길이 가드(±30%) 안이면 채택돼야 한다."""
    long_body = "부산의 여름을 기억한다. " * 300 + "[E1]"
    corrected = "부산의 여름을 기억한다. " * 299 + "교열된 마지막 문장이다."
    with (
        patch(
            "app.clients.solar.chat_completion",
            new=_routing_chat_completion(writing=long_body, proofread=corrected),
        ),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        autobiography = await _seed_pipeline_until_select(gateways)
        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)

        chapter = await autobiography_service.write_chapter(gateways, chapters[0].id)

    assert "교열된 마지막 문장이다." in (chapter.content or "")
