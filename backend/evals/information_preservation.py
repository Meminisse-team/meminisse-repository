"""
기획안 6절 "정보 보존율·사실 정합률 지표" — evals/README.md가 이제까지 다룬 4개
지표(합성 페르소나 벤치마크·DeepEval 라벨정확도·G-Eval 서사일관성·SUS)와는 별개로
전혀 손대지 않았던 항목이다.

기획안 원문: "원본 입력에서 TF-IDF 상위 명사와 개체명(인명·지명·연도·기관명) 전수의
합집합으로 핵심 키워드 풀을 구성하고, 형태소 분석 기반 표제어 정규화 매칭으로 최종
원고 내 생존율(정보 보존율, recall)을 측정한다. 동시에... 원본에 근거 없는 진술의
비율(사실 정합률, precision)을 함께 보고... 키워드 상위 30/50/100개 구간별 생존율
곡선을 병기".

이 프로젝트에서 두 가지를 의도적으로 대체·축소했다:

1. **TF-IDF+NER → Solar 구조화 추출로 대체.** 고전 TF-IDF는 통계적으로 유의미하려면
   비교 대상 코퍼스가 필요한데, 페르소나 1명의 세션 하나짜리 원본 입력(수백 자)에는
   적용할 대상 자체가 빈약하다. 별도 NER 라이브러리(spaCy 등)를 새로 들이는 대신,
   이 프로젝트가 이미 등장인물 검토(character_service.py, NER_EXTRACTION_SCHEMA)에서
   쓰는 것과 같은 방식 — Solar Structured Outputs로 핵심 키워드(중요 명사+개체명)를
   중요도 순으로 뽑는다. WeasyPrint(GTK 네이티브 의존성)처럼 새 무거운 의존성을 들일
   때마다 이 프로젝트가 겪은 설치 마찰을 반복하지 않기 위한 선택이다.

2. **형태소 분석기 → 정규식 기반 조사 제거로 축소.** kiwipiepy/konlpy 같은 실제
   형태소 분석기를 새로 들이는 대신, 흔한 한국어 조사를 정규식으로 잘라내는 가벼운
   근사치(_normalize)로 대체했다 — "표제어 정규화"의 정신은 살리되 완전한 형태소
   분석은 아니다. 이 근사가 놓치는 활용형(동사 어미 변화 등)이 있을 수 있어 recall이
   실제보다 낮게 나올 가능성이 있다 — 30명 규모로 늘릴 때 정밀도가 부족하다고
   판단되면 kiwipiepy 도입을 재검토할 것.

3. **키워드 풀 규모 축소(30/50/100 → 호출부가 지정, 기본 5/10/15).** 기획안의
   30/50/100은 12만 자 완성 원고 스케일(기획안 1.2.2절 단위비용 추정 참조)을
   전제한 컷오프다. 지금 합성 페르소나는 세션 하나(사건 하나)만 다루는 파일럿
   규모라 원본 입력 자체에 키워드가 30개도 안 나올 수 있다 — 30명 규모로 늘려
   실제 완성 원고 단위로 돌릴 때는 cutoffs를 (30, 50, 100)으로 올려야 기획안의
   원래 그림(생존율 곡선)이 나온다.

사실 정합률(precision)은 Layer 1 라벨 대조(app/services/autobiography_service.py의
_run_factcheck)를 재사용하지 않는다 — 이 지표는 baseline/ablation 비교(아래
baseline_and_ablations.py)에서 label-free 베이스라인(이벤트 추출을 아예 안 거치는
조건)에도 동일하게 적용돼야 하므로, Layer 1 이벤트 레코드가 아니라 원본 텍스트를
직접 참조하는 지표여야 한다. 그래서 evals/groundedness_gate_accuracy.py에서 이미
실측 검증한(solar-mini, false_grounded 0/10) app.clients.groundedness.check을
재사용해 "챕터의 각 문장이 원본 입력에 근거하는가"를 그대로 사실 정합률로 쓴다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel

from app.clients import groundedness, nli, solar

_KEYWORD_EXTRACTION_SYSTEM_PROMPT = """\
당신은 텍스트에서 핵심 키워드를 뽑는 분석기입니다. 아래 원본 텍스트에서
(1) 통계적으로 중요한(반복되거나 주제를 규정하는) 명사, (2) 개체명(인명·지명·
연도/시기·기관명) 전부를 합쳐, 만약 이 키워드들이 나중에 다시 쓴 글에서
사라진다면 원본의 정보 손실이라고 볼 수 있는 순서로 중요도 내림차순 정렬해
반환하세요. 조사는 제외한 어근/명사 형태로 반환하세요.
"""


class _KeywordPool(BaseModel):
    keywords: list[str]


def _build_keyword_extraction_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
        "required": ["keywords"],
        "additionalProperties": False,
    }


async def extract_keyword_pool(raw_input_text: str, *, top_k: int = 15) -> list[str]:
    """모듈 docstring 1번 참조 — TF-IDF 상위 명사 + 개체명 합집합의 대체 구현."""
    if not raw_input_text.strip():
        return []
    result = await solar.structured_completion(
        [
            {"role": "system", "content": _KEYWORD_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": raw_input_text},
        ],
        schema_name="keyword_pool",
        json_schema=_build_keyword_extraction_schema(),
        reasoning_effort="low",
    )
    return result["keywords"][:top_k]


# 흔한 한국어 조사를 어절 끝에서 제거한다 — 모듈 docstring 2번 참조(형태소
# 분석기 대체 근사). 긴 조사부터 매칭해야 짧은 조사가 먼저 걸려 일부만
# 잘려나가는 걸 방지한다(예: "에게서"가 "에게"보다 먼저 시도돼야 함).
_JOSA_SUFFIXES = sorted(
    [
        "으로써", "로써", "으로서", "로서", "에게서", "에게", "한테서", "한테",
        "이라고", "라고", "이라는", "라는", "까지", "부터", "이나", "에서",
        "으로", "이란", "란", "은", "는", "이", "가", "을", "를", "의", "에",
        "로", "과", "와", "도", "만",
    ],
    key=len,
    reverse=True,
)
_JOSA_PATTERN = re.compile("(?:" + "|".join(_JOSA_SUFFIXES) + ")$")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize(text: str) -> str:
    stripped_tokens = []
    for token in _WHITESPACE_PATTERN.split(text.strip()):
        if not token:
            continue
        stripped_tokens.append(_JOSA_PATTERN.sub("", token))
    return "".join(stripped_tokens)


def keyword_survives(keyword: str, final_content: str) -> bool:
    norm_keyword = _normalize(keyword)
    if not norm_keyword:
        return False
    return norm_keyword in _normalize(final_content)


def compute_recall_curve(
    keywords: list[str], final_content: str, *, cutoffs: tuple[int, ...] = (5, 10, 15)
) -> dict[str, dict[str, Any]]:
    """cutoffs 각 구간(상위 N개 키워드)에서의 생존율(recall)을 계산한다 —
    기획안의 "키워드 상위 30/50/100개 구간별 생존율 곡선"과 같은 형태(모듈
    docstring 3번 참조: 이 파일럿에서는 cutoff 자체를 축소해 호출)."""
    curve: dict[str, dict[str, Any]] = {}
    for cutoff in cutoffs:
        subset = keywords[:cutoff]
        if not subset:
            curve[str(cutoff)] = {"recall": None, "survived": 0, "total": 0}
            continue
        survived = [kw for kw in subset if keyword_survives(kw, final_content)]
        curve[str(cutoff)] = {
            "recall": len(survived) / len(subset),
            "survived": len(survived),
            "total": len(subset),
            "missing_keywords": [kw for kw in subset if kw not in survived],
        }
    return curve


_PRECISION_CONCURRENCY = 5  # groundedness.check(Solar API)를 문장 수만큼 무제한 동시 호출하면
# 429(요청 한도)를 유발한다(evals/followup_trigger_audit.py 실측 사례) — 상한을 둔다.


async def compute_precision(
    raw_input_text: str, final_content: str, *, sample_size: int | None = None
) -> dict[str, Any]:
    """모듈 docstring 마지막 문단 참조 — 챕터 문장 단위로 groundedness 2차
    게이트(solar-mini, evals/groundedness_gate_accuracy.py로 실측 검증됨)를
    재사용해 원본에 근거한 문장 비율을 사실 정합률로 낸다.

    로컬 NLI로 이 API 호출을 대체하는 방안을 2026-07-19에 시도했다가 걷어냈다
    — 같은 날 프로덕션 왜곡 탐지(event_extraction_service)에서 로컬 NLI가
    세션당 190~210초(GPU 없는 개발 환경)로 실측돼 Solar LLM 판정으로 교체되고
    app/clients/nli.py의 모델 로딩 코드 자체가 삭제됐다(요청한 사람이 기대한
    "로컬이라 빠르고 무료"라는 전제가 이 환경에서는 거짓으로 판명됨) —
    이 함수가 의존하던 nli.classify_entailment_batch도 함께 사라져 즉시
    AttributeError로 깨졌다. "시간·비용이 빠듯하면 NLI" 같은 대안은 이 환경에서는
    성립하지 않는다 — Solar API 호출(이 함수)이 사실상 유일한 선택지다. 비용을
    줄이려면 sample_size로 문장 수를 제한하는 것이 현실적인 레버다."""
    sentences = nli.split_sentences(final_content)
    if sample_size is not None:
        sentences = sentences[:sample_size]
    if not sentences or not raw_input_text.strip():
        return {"precision": None, "sentence_count": len(sentences), "grounded_count": 0, "ungrounded_sentences": []}

    semaphore = asyncio.Semaphore(_PRECISION_CONCURRENCY)

    async def _check(sentence: str) -> str:
        async with semaphore:
            return await groundedness.check(context=raw_input_text, answer=sentence)

    verdicts = await asyncio.gather(*(_check(s) for s in sentences))
    grounded_flags = [v == groundedness.GROUNDED for v in verdicts]
    ungrounded = [s for s, ok in zip(sentences, grounded_flags) if not ok]
    return {
        "precision": sum(grounded_flags) / len(sentences),
        "sentence_count": len(sentences),
        "grounded_count": sum(grounded_flags),
        "ungrounded_sentences": ungrounded,
    }


async def evaluate_manuscript(
    *,
    raw_input_text: str,
    final_content: str,
    cutoffs: tuple[int, ...] = (5, 10, 15),
    precision_sample_size: int | None = None,
) -> dict[str, Any]:
    keywords = await extract_keyword_pool(raw_input_text, top_k=max(cutoffs))
    recall_curve = compute_recall_curve(keywords, final_content, cutoffs=cutoffs)
    precision_report = await compute_precision(raw_input_text, final_content, sample_size=precision_sample_size)
    return {
        "keyword_pool": keywords,
        "recall_curve": recall_curve,
        "precision": precision_report["precision"],
        "precision_detail": precision_report,
    }


def raw_input_text_from_persona_result(persona_result: dict[str, Any]) -> str:
    """벤치마크 결과 JSON(evals/run_benchmark.py 출력)에서 "원본 입력"에 해당하는
    텍스트를 재구성한다 — 화자(페르소나)가 실제로 발화한 user 턴만 모은다
    (assistant 질문은 화자가 제공한 정보가 아니므로 원본 정보량에 포함하지 않는다,
    event_extraction_service._passes_distortion_check와 동일한 관례)."""
    transcript = persona_result.get("transcript", [])
    return "\n".join(turn["content"] for turn in transcript if turn.get("role") == "user")
