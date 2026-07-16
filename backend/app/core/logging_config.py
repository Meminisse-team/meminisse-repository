"""
파일 기반 로깅 설정.

기존에는 backend(uvicorn)와 worker/beat(Celery, Docker) 모두 콘솔(stderr)에만
로그를 찍었다 — 관리자 대시보드에서 "지금 뭐가 도는지" 확인하려면 매번 터미널
창이나 `docker logs`를 직접 열어야 했다. 콘솔 출력은 그대로 두고, 같은 내용을
파일에도 남겨 관리자 대시보드(app/services/admin_service.py:get_app_log_lines,
GET /api/v1/admin/logs)가 마지막 N줄을 읽어 보여줄 수 있게 한다.

backend는 로컬에서 `backend/` 기준으로 실행되므로(backend/run-backend.bat)
`backend/logs/backend.log`에 쓴다. worker/beat는 Docker 컨테이너 안에서
`/app/logs/{worker,beat}.log`에 쓰는데, docker-compose.yml이 `./logs:/app/logs`를
볼륨 마운트해 호스트의 같은 `backend/logs/` 디렉터리로 이어진다 — 세 로그가
전부 한 디렉터리에 모인다.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_configured_services: set[str] = set()


def configure_file_logging(service_name: str) -> None:
    """루트 로거에 RotatingFileHandler를 추가한다. 기존 콘솔 핸들러는 건드리지
    않는다 — 파일 로깅은 추가일 뿐 대체가 아니다. 같은 프로세스에서 두 번
    호출돼도(예: 워커 재시작 시그널이 여러 번 발생) 핸들러가 중복으로 쌓이지
    않도록 서비스명 단위로 한 번만 적용한다."""
    if service_name in _configured_services:
        return
    _configured_services.add(service_name)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_DIR / f"{service_name}.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
