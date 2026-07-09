"""
Celery + Redis 기반 비동기 워커 (기획안 4절: "세션 종료 후 이벤트 추출, 최종 집필,
PDF 조판 등 수 분 이상 소요되는 무거운 연산은 Celery + Redis 메시지 큐 기반의 독립
워커에서 처리하여 API 서버의 타임아웃을 원천 차단한다").

세션 요약 갱신 등 가벼운 후처리는 FastAPI BackgroundTasks로 처리해 인프라 복잡도를
낮춘다는 것이 기획 의도이므로, 여기 등록하는 태스크는 "수 분 이상 걸릴 수 있는 작업"
(세션 후처리, Phase 3/4 파이프라인, 조판)에 한정한다.
"""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "meminisse",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
    task_track_started=True,
)
