"""_strip_citation_tags — 집필 근거 태그([E1]...) 회수·검증 단위 테스트.

집필 프롬프트(CHAPTER_WRITING_SYSTEM_PROMPT)는 사실 서술 문단 끝에 근거 사건
태그를 달도록 강제하고, 서비스 레이어는 저장 전에 태그를 제거하면서 "유효한
태그가 하나도 없는 문단"을 근거검증 판정자의 집중 검토 대상으로 수집한다.
"""

from __future__ import annotations

from app.services.autobiography_service import _strip_citation_tags


def test_strips_tags_and_reports_no_uncited_when_all_paragraphs_cited() -> None:
    content = "부산에서 태어났다. [E1]\n\n학교에 다녔다. [E2][E3]"
    cleaned, uncited = _strip_citation_tags(content, event_count=3)
    assert cleaned == "부산에서 태어났다.\n\n학교에 다녔다."
    assert uncited == []


def test_collects_paragraphs_without_any_tag() -> None:
    content = "부산에서 태어났다. [E1]\n\n그 시절이 그립다."
    cleaned, uncited = _strip_citation_tags(content, event_count=1)
    assert "[E1]" not in cleaned
    assert uncited == ["그 시절이 그립다."]


def test_tag_outside_event_range_counts_as_uncited_but_is_still_removed() -> None:
    """존재하지 않는 사건 번호([E9] 등)를 인용한 문단은 근거가 확인되지 않은
    문단으로 취급한다 — 다만 태그 자체는 어떤 경우에도 본문에서 제거되어야
    한다(PDF 인쇄 안전)."""
    content = "이상한 주장을 했다. [E9]"
    cleaned, uncited = _strip_citation_tags(content, event_count=3)
    assert cleaned == "이상한 주장을 했다."
    assert uncited == ["이상한 주장을 했다."]


def test_mid_sentence_tags_do_not_leave_double_spaces() -> None:
    content = "부산에서 [E1] 태어났다."
    cleaned, uncited = _strip_citation_tags(content, event_count=1)
    assert cleaned == "부산에서 태어났다."
    assert uncited == []


def test_empty_paragraphs_after_stripping_are_dropped() -> None:
    content = "부산에서 태어났다. [E1]\n\n[E1]\n\n학교에 다녔다. [E1]"
    cleaned, uncited = _strip_citation_tags(content, event_count=1)
    assert cleaned == "부산에서 태어났다.\n\n학교에 다녔다."
    assert uncited == []
