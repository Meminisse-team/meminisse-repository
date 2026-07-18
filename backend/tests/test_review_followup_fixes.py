"""2026-07-18 전체 코드 검토 후속 수정 회귀 테스트.

1. 통일성 윤문의 챕터별 되써넣기(finalize_manuscript 마커 파싱) — 윤문 결과가
   chapter.content에 반영돼 PDF와 웹 열람이 같은 텍스트를 보게 됐는지.
2. 팩트체크 prose 폴백 — 라벨에는 없지만 소환된 사건 원문에 실재하는 사실이
   더는 플래그되지 않는지.
3. 배정 시기 보정(_rebalance_assignment_by_year) — 검색 순위가 시기를 무시하고
   엉뚱한 챕터에 배정한 사건이 연도 중앙값 기준으로 재배치되는지.
4. 왜곡 탐지 실패 후속 처리 — 재시도 1회, 재실패 시 distortion_flagged 저장,
   사용자 산문 편집 시 해제.
5. '꼭 넣기' 전파 — 세션 토글이 기존 이벤트와 신규 추출 이벤트 모두에 반영되는지.
6. get_opening_contents 배치 조회('나의 이야기' 카드 제목 N+1 제거).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.gateways.dto import EventCreateData, EventRecord, SessionCreateData
from app.gateways.factory import Gateways, _build_mock_gateways
from app.models.enums import EventSourceType, MessageRole, SessionType
from app.schemas.user import UserCreate
from app.services import event_extraction_service, interview_service, story_service, user_service
from app.services.autobiography_service import (
    _rebalance_assignment_by_year,
    _run_factcheck,
    _split_revised_manuscript_by_chapter,
    _strip_prompt_section_echo,
)


async def _fake_admin_create_user(*, email: str, password: str, user_metadata: dict) -> uuid.UUID:
    return uuid.uuid4()


def _make_event(
    *, year: int | None = None, occurred_at_label: str | None = None,
    prose: str = "문단", place: str | None = None, people: str | None = None,
) -> EventRecord:
    return EventRecord(
        id=uuid.uuid4(), user_id=uuid.uuid4(), source_type=EventSourceType.SESSION_CHAT,
        session_id=None, media_asset_id=None, source_span=None, life_period=None,
        occurred_at_label=occurred_at_label, place=place, people=people,
        one_line_summary="요약", prose_paragraph=prose, emotion_tag=None,
        emotion_intensity=None, emotion_inferred=False,
        labels={"estimated_year_start": year} if year is not None else {},
        confidence=None, verified=True, is_must_include=False, embedding=None,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. finalize 마커 파싱
# ---------------------------------------------------------------------------


def test_split_revised_manuscript_parses_chapters_and_strips_headers() -> None:
    revised = (
        "<<<CHAPTER 1>>>\n[1장. 어린 시절]\n윤문된 1장 본문이다.\n\n"
        "=== PART 2: 성년 ===\n\n"
        "<<<CHAPTER 2>>>\n[2장. 성년]\n윤문된 2장 본문이다."
    )
    result = _split_revised_manuscript_by_chapter(revised, [1, 2])
    assert result == {1: "윤문된 1장 본문이다.", 2: "윤문된 2장 본문이다."}


def test_split_revised_manuscript_returns_none_on_marker_mismatch() -> None:
    # 마커 누락(2장 없음) → 파싱 실패 → 호출부가 보수적 폴백을 타야 한다.
    revised = "<<<CHAPTER 1>>>\n[1장. 어린 시절]\n본문."
    assert _split_revised_manuscript_by_chapter(revised, [1, 2]) is None
    # 마커 순서/번호 불일치도 실패.
    swapped = "<<<CHAPTER 2>>>\n본문.\n<<<CHAPTER 1>>>\n본문."
    assert _split_revised_manuscript_by_chapter(swapped, [1, 2]) is None


def test_strip_prompt_section_echo_removes_leading_instruction_blocks() -> None:
    # 검수 모델이 user 메시지의 지시 블록·헤더를 출력에 그대로 되돌린 실측 사례
    # (2026-07-18, 7장 본문에 "[완화 대상 반복 표현 …]" 블록이 통째로 저장됨).
    echoed = (
        "[완화 대상 반복 표현 — 본문에서 4회 이상 등장한 단어들입니다]\n작은, 크게\n\n"
        "[챕터 본문]\n교열된 본문이다."
    )
    assert _strip_prompt_section_echo(echoed, body_marker="[챕터 본문]") == "교열된 본문이다."
    # 에코가 없으면 원문 유지(공백 정리만).
    assert _strip_prompt_section_echo("  깨끗한 본문.  ", body_marker="[챕터 본문]") == "깨끗한 본문."


# ---------------------------------------------------------------------------
# 2. 팩트체크 prose 폴백
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factcheck_falls_back_to_source_prose_before_flagging() -> None:
    events = [
        _make_event(prose="킵 손과 내기를 했다.", people="제인"),  # 라벨엔 킵 손 없음
    ]

    async def _fake_structured(messages, *, schema_name, json_schema, **kwargs):
        assert schema_name == "fact_reextraction"
        return {
            "facts": [
                {"fact_type": "person", "raw_text": "킵 손"},   # 원문에 있음 → 통과해야
                {"fact_type": "person", "raw_text": "일레인"},  # 어디에도 없음 → 플래그
            ]
        }

    with patch("app.clients.solar.structured_completion", new=_fake_structured):
        report = await _run_factcheck("본문", source_events=events, birth_year=None)

    flagged = [f["raw_text"] for f in report["flags"]]
    assert flagged == ["일레인"]


# ---------------------------------------------------------------------------
# 3. 배정 시기 보정
# ---------------------------------------------------------------------------


def test_rebalance_moves_year_outlier_to_closer_chapter() -> None:
    # 챕터 A(1967 중심)에 1979년 사건이 순위 때문에 배정됐고, 그 사건은 챕터 B
    # (1979 중심)의 검색 결과에도 있었다 → B로 이동해야 한다.
    outlier = _make_event(year=1979)
    a1, a2 = _make_event(year=1966), _make_event(year=1968)
    b1, b2 = _make_event(year=1978), _make_event(year=1980)
    assigned = [[a1, a2, outlier], [b1, b2]]
    retrieved = [[a1, a2, outlier], [outlier, b1, b2]]

    rebalanced = _rebalance_assignment_by_year(assigned, retrieved)

    assert outlier.id not in {e.id for e in rebalanced[0]}
    assert outlier.id in {e.id for e in rebalanced[1]}
    # 결정론: 같은 입력이면 같은 출력.
    again = _rebalance_assignment_by_year(assigned, retrieved)
    assert [[e.id for e in ch] for ch in again] == [[e.id for e in ch] for ch in rebalanced]


def test_rebalance_uses_occurred_at_label_year_fallback() -> None:
    # labels에 정규화 연도가 없어도 occurred_at_label 속 4자리 연도로 판단한다.
    outlier = _make_event(occurred_at_label="1979년 케임브리지")
    a1, a2 = _make_event(year=1966), _make_event(year=1967)
    b1, b2 = _make_event(year=1979), _make_event(year=1980)
    rebalanced = _rebalance_assignment_by_year(
        [[a1, a2, outlier], [b1, b2]],
        [[a1, a2, outlier], [outlier, b1, b2]],
    )
    assert outlier.id in {e.id for e in rebalanced[1]}


def test_rebalance_never_moves_event_not_retrieved_by_target_chapter() -> None:
    # 다른 챕터 검색 결과에 없던 사건은 아무리 이격이 커도 옮기지 않는다.
    outlier = _make_event(year=2000)
    a1, a2 = _make_event(year=1966), _make_event(year=1967)
    b1 = _make_event(year=2000)
    rebalanced = _rebalance_assignment_by_year(
        [[a1, a2, outlier], [b1]],
        [[a1, a2, outlier], [b1]],  # outlier는 챕터 B 검색 결과에 없음
    )
    assert outlier.id in {e.id for e in rebalanced[0]}


# ---------------------------------------------------------------------------
# 4·5·6. 왜곡 플래그 / 꼭 넣기 / 오프닝 배치 조회 (mock 게이트웨이 통합)
# ---------------------------------------------------------------------------


async def _seed_completed_session(gateways: Gateways, *, prose_ready: bool = False):
    user = await user_service.create_user(
        gateways, UserCreate(email=f"u{uuid.uuid4().hex[:10]}@example.com", name="테스터", password="test-password-123")
    )
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.add_chat_log(
        session.id, role=MessageRole.ASSISTANT, content="어린 시절 이야기를 들려주세요."
    )
    await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content="부산에서 자랐어요.")
    if prose_ready:
        await gateways.sessions.set_session_prose(session.id, "나는 부산에서 자랐다.")
    await gateways.sessions.complete(session.id)
    await gateways.commit()
    return user, session


@pytest.mark.asyncio
async def test_distortion_failure_retries_then_flags_and_user_edit_clears() -> None:
    reassembly_calls = {"n": 0}

    async def _fake_chat(messages, **kwargs):
        reassembly_calls["n"] += 1

        class _R:
            choices = [type("C", (), {"message": type("M", (), {"content": "지어낸 재조립본."})()})()]

        return _R()

    async def _always_distorted(*, original_turns, reassembled_prose):
        return False

    async def _fake_structured(messages, *, schema_name, json_schema, **kwargs):
        return {"events": [], "relations": []}

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat),
        patch("app.clients.solar.structured_completion", new=_fake_structured),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
        patch(
            "app.services.event_extraction_service._passes_distortion_check",
            new=_always_distorted,
        ),
    ):
        gateways = _build_mock_gateways()
        user, session = await _seed_completed_session(gateways)

        events = await event_extraction_service.process_completed_session(gateways, session.id)

        assert events == []
        assert reassembly_calls["n"] == 2  # 1차(medium) + 재시도(high)
        flagged = await gateways.sessions.get_by_id(session.id)
        assert flagged.distortion_flagged is True
        assert flagged.session_prose  # 산문 자체는 저장된다

        # '나의 이야기' 카드에 플래그가 노출된다.
        page = await story_service.list_story_cards(gateways, user.id, limit=10, offset=0)
        assert page.items[0].distortion_flagged is True

        # 사용자가 산문을 직접 수정·저장하면(사람이 확정한 텍스트) 플래그 해제 +
        # 이벤트 재추출 경로가 정상 동작한다.
        card = await story_service.update_session_prose(
            gateways, user.id, session.id, "부산에서 자란 이야기를 정리했다."
        )
        assert card.distortion_flagged is False
        cleared = await gateways.sessions.get_by_id(session.id)
        assert cleared.distortion_flagged is False


@pytest.mark.asyncio
async def test_must_include_toggle_propagates_to_existing_and_new_events() -> None:
    async def _fake_structured(messages, *, schema_name, json_schema, **kwargs):
        assert schema_name == "event_extraction"
        return {
            "events": [
                {
                    "one_line_summary": "부산 유년", "prose_paragraph": "부산에서 자랐다.",
                    "place": "부산", "occurred_at_label": "어린 시절",
                    "estimated_year_start": None, "estimated_year_end": None,
                    "people": "혼자", "event_subject": "narrator",
                    "emotion_tag": None, "emotion_intensity": None, "emotion_inferred": False,
                    "values_reflected": None, "reason": None, "process": None,
                    "gratitude": None, "regret": None, "turning_point": None,
                    "pride": None, "belief": None, "message": None,
                    "source_quote": "부산에서 자랐다.",
                    "place_confidence": 0.9, "occurred_at_confidence": 0.5,
                }
            ],
            "relations": [],
        }

    with (
        patch("app.clients.solar.structured_completion", new=_fake_structured),
        patch("app.clients.embeddings.embed_passages", return_value=[[1.0, 0.0]]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
    ):
        gateways = _build_mock_gateways()
        user, session = await _seed_completed_session(gateways, prose_ready=True)

        # 기존 이벤트 하나를 심는다(토글 시 함께 갱신돼야 함).
        existing = await gateways.events.create(
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT,
                session_id=session.id, one_line_summary="기존", prose_paragraph="기존 문단",
                verified=True,
            )
        )
        await gateways.commit()

        updated = await interview_service.set_must_include(gateways, session, True)
        assert updated.is_must_include is True
        refreshed = await gateways.events.list_by_session(session.id)
        assert all(e.is_must_include for e in refreshed)
        assert existing.id in {e.id for e in refreshed}

        # 토글 이후의 (재)추출도 세션 플래그를 상속한다.
        new_events = await event_extraction_service.reextract_events_from_edited_prose(
            gateways, session.id
        )
        assert new_events and all(e.is_must_include for e in new_events)

        # 해제도 전파된다.
        await interview_service.set_must_include(gateways, updated, False)
        assert all(not e.is_must_include for e in await gateways.events.list_by_session(session.id))


@pytest.mark.asyncio
async def test_get_opening_contents_batches_titles() -> None:
    with patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user):
        gateways = _build_mock_gateways()
        user, session = await _seed_completed_session(gateways, prose_ready=True)

        openings = await gateways.sessions.get_opening_contents([session.id, uuid.uuid4()])
        assert openings == {session.id: "어린 시절 이야기를 들려주세요."}

        page = await story_service.list_story_cards(gateways, user.id, limit=10, offset=0)
        assert page.items[0].title == "어린 시절 이야기를 들려주세요."
