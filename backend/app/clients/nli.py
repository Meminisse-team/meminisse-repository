"""
로컬 한국어/다국어 NLI(자연어 추론) 모델 클라이언트.

기획안 6절: "NLI 기반 검증(공개 한국어 NLI 모델의 로컬 추론)... 비용 항에서 제외" —
이 모듈이 그 로컬 추론을 실제로 수행한다. Upstage API(app/clients/solar.py 등)처럼
원격 호출이 아니라 이 서버 프로세스 안에서 직접 모델을 돌리므로, 사용자는 아무것도
설치·다운로드하지 않는다(서버 배포 시 모델 가중치를 한 번 받아두면 되는 순수
인프라 비용이며, Upstage API를 호출하는 것과 구조적으로 동일하게 "서버가 대신
연산하고 결과만 응답"한다).

모델: MoritzLaurer/mDeBERTa-v3-base-mnli-xnli — XNLI로 학습된 다국어 NLI 모델로
한국어를 지원한다(실제 한국어 문장 쌍으로 entailment/neutral/contradiction 판정을
검증 완료, 2026-07-11). transformers의 동기 추론을 asyncio.to_thread로 감싸 이벤트
루프를 막지 않는다(app/clients/s3.py의 boto3 래핑과 동일한 패턴).

app/services/event_extraction_service.py(왜곡 탐지)와
app/services/autobiography_service.py(근거 검증) 양쪽에서 이 모듈을 사용한다.
"""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache

MODEL_NAME = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"

# premise+hypothesis 합산 기준. 초과분은 토크나이저가 자른다(truncation=True) —
# 세션 전체 원문처럼 긴 텍스트를 통째로 넣으면 뒷부분이 무시될 수 있으므로, 호출부가
# 문장/문단 단위로 쪼개 호출하는 것을 전제로 설계했다(아래 split_sentences 참조).
_MAX_LENGTH = 512

# 문장 경계: 마침표·물음표·느낌표 뒤 공백/줄바꿈. 완벽한 문장 분리기는 아니지만
# NLI 청크 단위로는 충분하다 — 잘못 나뉜 조각이 있어도 개별 판정에 영향을 줄 뿐,
# 전체 검증 로직을 깨뜨리지 않는다.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?다요까])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text.strip()) if s.strip()]


@lru_cache(maxsize=1)
def _load():
    # 지연 임포트: 이 무거운 의존성(torch/transformers)은 실제로 NLI를 처음 쓸 때만
    # 로드되게 해, 이 모듈을 import하는 것만으로 서버 기동이 느려지지 않게 한다.
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    model.eval()
    return tokenizer, model


def _classify_sync(premise: str, hypothesis: str) -> dict[str, float]:
    import torch

    tokenizer, model = _load()
    inputs = tokenizer(
        premise, hypothesis, return_tensors="pt", truncation=True, max_length=_MAX_LENGTH
    )
    with torch.no_grad():
        logits = model(**inputs).logits
    probs = torch.softmax(logits, dim=-1)[0]
    return {model.config.id2label[i]: float(p) for i, p in enumerate(probs)}


async def classify_entailment(*, premise: str, hypothesis: str) -> dict[str, float]:
    """premise가 hypothesis를 함의(entailment)/무관(neutral)/모순(contradiction)
    중 무엇으로 관계 맺는지 확률 분포로 반환한다.
    예: {"entailment": 0.91, "neutral": 0.08, "contradiction": 0.01}
    """
    return await asyncio.to_thread(_classify_sync, premise, hypothesis)
