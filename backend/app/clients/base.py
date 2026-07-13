"""
Upstage Chat Completions / Embeddings는 OpenAI SDK와 호환되므로(upstage_solar_api_docs.txt,
upstage_embeddings_api_docs.txt의 "OpenAI SDK Compatible" 섹션 참조) base_url만 갈아끼운
AsyncOpenAI 클라이언트 하나를 Solar와 Embeddings가 함께 재사용한다. Document Parse는
multipart/form-data 업로드가 필요해 OpenAI SDK로 감쌀 수 없으므로 clients/document_parse.py
에서 httpx로 별도 처리한다.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from app.config import settings

UPSTAGE_BASE_URL = "https://api.upstage.ai/v1"

# SDK 기본값(연결 5초/전체 600초)은 응답이 없는 연결을 최대 10분까지 붙잡아 둘 수 있다 —
# 합성 페르소나 벤치마크(evals/run_benchmark.py) 실행 중 실제로 Solar 호출 하나가
# established 상태로 멈춰 CPU 사용량이 더 늘지 않는 채 수 분간 진행이 안 되는 걸
# 재현했다(2026-07-12). 사용자 요청 경로에서도 같은 문제가 나면 요청 하나가 몇 분씩
# 붙잡힐 수 있으므로, 실패를 더 빨리 드러내도록 타임아웃을 줄인다.
_REQUEST_TIMEOUT = 90.0

# SDK 기본값(max_retries=2)을 그대로 두면 호출 하나가 최악의 경우 타임아웃×(1+재시도)
# ≈ 90초×3 = 270초까지 걸릴 수 있다 — evals/run_benchmark.py의 스테이지별 120초
# 타임아웃(_STAGE_TIMEOUT_SECONDS)이나 호출부의 재시도 전략과 이중으로 겹쳐 지연이
# 예측 불가능하게 증폭된다. 재시도가 필요한 호출부(있다면)는 스스로 재시도 정책을
# 갖춰야 한다 — SDK가 그 위에 또 재시도를 얹으면 "몇 분씩 멈춘다"는 증상의 원인 중
# 하나가 될 수 있다(간헐적 후처리 지연의 근본 원인 자체는 여전히 미상 —
# evals/README.md 참조. 이건 원인 규명이 아니라 지연 증폭 요인 하나를 제거하는
# 조치다).
_MAX_RETRIES = 0


@lru_cache(maxsize=1)
def get_upstage_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.UPSTAGE_API_KEY,
        base_url=UPSTAGE_BASE_URL,
        timeout=_REQUEST_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )
