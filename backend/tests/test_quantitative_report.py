"""evals/quantitative_report.py의 순수 로직(구조 통계 집계) 회귀 테스트. 실제
원고 생성·NLI·G-Eval 채점은 evals/README.md에 문서화된 스크립트 실행으로
검증한다(비용/시간 — 완성 원고 생성 자체가 필요)."""

from __future__ import annotations

from evals.quantitative_report import _structural_stats


class _FakeChapter:
    def __init__(self, content: str | None) -> None:
        self.content = content


def test_structural_stats_computes_part_and_chapter_counts() -> None:
    toc_data = {
        "candidates": [{"parts": [{"part_index": 1}, {"part_index": 2}, {"part_index": 3}], "chapters": []}],
        "selected_candidate_index": 0,
    }
    chapters = [_FakeChapter("가" * 1000), _FakeChapter("나" * 2000), _FakeChapter("다" * 1500)]
    stats = _structural_stats(toc_data, chapters)
    assert stats["part_count"] == 3
    assert stats["chapter_count"] == 3
    assert stats["chapter_length_mean"] == 1500
    assert stats["chapter_length_min"] == 1000
    assert stats["chapter_length_max"] == 2000


def test_structural_stats_handles_missing_toc_data() -> None:
    chapters = [_FakeChapter("가" * 500)]
    stats = _structural_stats(None, chapters)
    assert stats["part_count"] is None
    assert stats["chapter_count"] == 1
    assert stats["chapter_length_stdev"] is None  # 챕터 1개뿐이라 표준편차 계산 불가


def test_structural_stats_handles_no_chapters() -> None:
    stats = _structural_stats({"candidates": [], "selected_candidate_index": None}, [])
    assert stats["chapter_count"] == 0
    assert stats["chapter_length_mean"] is None


def test_structural_stats_ignores_chapters_with_empty_content() -> None:
    chapters = [_FakeChapter(None), _FakeChapter("가" * 800)]
    stats = _structural_stats(None, chapters)
    assert stats["chapter_count"] == 2  # 챕터 수는 그대로 세되
    assert stats["chapter_length_mean"] == 800  # 분량 통계에서는 content 없는 것 제외
