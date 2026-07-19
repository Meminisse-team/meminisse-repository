"""
문장 분리 유틸리티.

원래는 로컬 한국어/다국어 NLI(mDeBERTa) 모델 클라이언트였다 — event_extraction_
service._passes_distortion_check(산문 재조립 왜곡 탐지)가 문장 단위 entailment
배치 판정에 썼다. 실사용 중 세션 하나에 190~210초(로컬 CPU 추론)가 걸려 처리
파이프라인의 스테이지 타임아웃을 반복적으로 넘기는 문제가 확인돼(2026-07-19),
autobiography_service._run_groundedness_check가 겪었던 같은 문제(그쪽은 "챕터
하나에 20분", 2026-07-17)와 동일하게 Solar LLM 판정(app/agents/prompts.py
DISTORTION_CHECK_SYSTEM_PROMPT, solar-mini)으로 교체됐다 — 이제 로컬 모델
추론은 이 프로젝트 어디에서도 하지 않는다(requirements.txt의 torch/transformers도
함께 제거됨).

split_sentences만 다른 곳(_strip_leaked_assistant_sentences)에서 계속 쓰이므로
남긴다 — 모델과 무관한 순수 정규식 유틸리티다.
"""

from __future__ import annotations

import re

# 문장 경계: 마침표·물음표·느낌표 뒤 공백/줄바꿈. 완벽한 문장 분리기는 아니지만
# 이 용도(문장 단위로 쪼개 assistant 원문과 부분 문자열 대조)로는 충분하다 —
# 잘못 나뉜 조각이 있어도 개별 판정에 영향을 줄 뿐, 전체 로직을 깨뜨리지 않는다.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?다요까])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text.strip()) if s.strip()]
