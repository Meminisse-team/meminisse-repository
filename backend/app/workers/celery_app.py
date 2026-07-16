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
    # reconcile_stale_sessions(app/workers/tasks.py)를 5분마다 실행 — 세션 완료
    # 처리 큐잉이 실패한 채(브로커 순간 다운 등, app/workers/enqueue.py의 즉시
    # 재시도까지 전부 실패한 경우) 방치되는 걸 사람 개입 없이 스스로 복구한다
    # (2026-07-15 사고: 세션 6개가 방치돼 관리자가 수동으로 찾아 재큐잉해야
    # 했음 — 이제 최악의 경우에도 5분 안에 자동 복구된다). docker-compose.yml의
    # 별도 beat 서비스가 이 스케줄을 실제로 발행한다 — worker 프로세스 자체는
    # Beat를 겸하지 않는다(Celery의 표준 구성: beat는 스케줄대로 메시지를
    # 발행만 하고, worker가 그 메시지를 받아 실행한다).
    beat_schedule={
        "reconcile-stale-sessions": {
            "task": "reconcile_stale_sessions",
            "schedule": 300.0,  # 5분
        },
    },
)
