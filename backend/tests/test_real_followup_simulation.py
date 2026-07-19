"""evals/real_followup_simulation.py의 순수 로직(헤더 파싱, 사람이 편집한 감사
파일 변환) 회귀 테스트. 실제 시뮬레이션(Solar 호출), DB 시딩, Phase 2 재처리는
evals/real_data_comparison.py --file/--followup-audit-file 인자로 실행되는
절차로 검증한다(비용/부작용 — 계정 생성 포함)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.real_followup_simulation import build_augmented_qa_from_audit_file, extract_profile_header


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


def _write_audit_file(tmp_path: Path, results: list[dict]) -> Path:
    path = tmp_path / "followup_audit_test.json"
    path.write_text(
        json.dumps({"name": "테스트 인물", "summary": {}, "results": results}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_build_augmented_qa_from_audit_file_uses_provided_answers(tmp_path: Path) -> None:
    path = _write_audit_file(
        tmp_path,
        [
            {
                "number": 1,
                "question": "질문1",
                "answer": "답변1",
                "category": "필수슬롯형_꼬리질문",
                "followup_question": "몇 살이었나요?",
                "followup_answer": "사람이 직접 채운 답변",
            },
            {
                "number": 2,
                "question": "질문2",
                "answer": "답변2",
                "category": "발동없음",
                "followup_question": None,
                "followup_answer": None,
            },
        ],
    )
    augmented = build_augmented_qa_from_audit_file(path)
    assert augmented[0]["followup_answer"] == "사람이 직접 채운 답변"
    assert augmented[0]["followup_question"] == "몇 살이었나요?"
    assert augmented[1]["followup_question"] is None
    assert augmented[1]["followup_answer"] is None


def test_build_augmented_qa_from_audit_file_rejects_unanswered_followups(tmp_path: Path) -> None:
    path = _write_audit_file(
        tmp_path,
        [
            {
                "number": 5,
                "question": "질문5",
                "answer": "답변5",
                "category": "필수슬롯형_꼬리질문",
                "followup_question": "누구와 함께였나요?",
                "followup_answer": None,  # 사람이 아직 안 채움
            }
        ],
    )
    with pytest.raises(ValueError, match=r"\[5\]"):
        build_augmented_qa_from_audit_file(path)


def test_build_augmented_qa_from_audit_file_rejects_empty_string_answer(tmp_path: Path) -> None:
    path = _write_audit_file(
        tmp_path,
        [
            {
                "number": 7,
                "question": "질문7",
                "answer": "답변7",
                "category": "분량부족형_꼬리질문",
                "followup_question": "더 자세히 말해주실 수 있나요?",
                "followup_answer": "",  # 빈 문자열도 "안 채움"으로 취급해야 함
            }
        ],
    )
    with pytest.raises(ValueError):
        build_augmented_qa_from_audit_file(path)
