"""evals/real_followup_simulation.py의 순수 로직(헤더 파싱) 회귀 테스트. 실제
시뮬레이션(Solar 호출), DB 시딩, Phase 2 재처리는 evals/real_data_comparison.py
--file 인자로 실행되는 절차로 검증한다(비용/부작용 — 계정 생성 포함)."""

from __future__ import annotations

from pathlib import Path

from evals.real_followup_simulation import extract_profile_header


def test_extract_profile_header_returns_text_before_first_question(tmp_path: Path) -> None:
    content = (
        "빌게이츠 (v2)\n\n"
        "출생 연도: 1955년\n고향(출생지): 미국 워싱턴주 시애틀\n"
        "[질문 1] 첫 질문입니다.\n[답변 1] 첫 답변입니다.\n"
    )
    file_path = tmp_path / "test.txt"
    file_path.write_text(content, encoding="utf-8")

    header = extract_profile_header(file_path)
    assert "출생 연도: 1955년" in header
    assert "고향(출생지)" in header
    assert "[질문 1]" not in header
    assert "첫 답변입니다" not in header


def test_extract_profile_header_falls_back_when_no_question_marker(tmp_path: Path) -> None:
    file_path = tmp_path / "no_questions.txt"
    file_path.write_text("그냥 프로필 텍스트만 있음", encoding="utf-8")
    header = extract_profile_header(file_path)
    assert header == "그냥 프로필 텍스트만 있음"
