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

# SDK 기본값(max_retries=2)을 0으로 낮췄던 이유는 "느린/멈춘 연결"이 타임아웃×
# (1+재시도)만큼 겹겹이 늘어나 최악의 경우 270초까지 걸리는 걸 막기 위함이었다
# (evals/README.md 참조). 그런데 실사용 중(2026-07-15, Celery 워커로 완료된 세션
# 6개를 몰아서 재처리) OpenAIError('Connection error.')가 매번 1~2초 만에(90초
# 타임아웃과 무관하게) 거의 절반 확률로 발생하는 걸 재현했다 — 이건 "느린 연결"이
# 아니라 커넥션 풀에 재사용되던 커넥션이 그 사이 서버/중간 인프라에 의해 이미
# 끊겨 있는데(idle 커넥션이 한동안 재사용 안 되다 죽는 흔한 패턴), 재시도가
# 아예 없어 그대로 실패하는 것이다. 1회 재시도는 이런 빠른 연결 실패는 즉시
# 복구하면서도, 최악의 경우(진짜 느린/멈춘 연결)도 90초×2=180초로 여전히 원래
# 우려했던 270초보다 짧다 — 그래서 0이 아니라 1로 둔다.
_MAX_RETRIES = 1


@lru_cache(maxsize=1)
def get_upstage_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.UPSTAGE_API_KEY,
        base_url=UPSTAGE_BASE_URL,
        timeout=_REQUEST_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )
