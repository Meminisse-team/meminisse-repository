# Celery 워커 전용 이미지 (app/workers/celery_app.py, docker-compose.yml의 worker
# 서비스). FastAPI API 서버는 아직 이 이미지로 옮기지 않았다 — 지금까지 팀이 로컬
# `.venv`로 `fastapi dev`를 직접 띄워온 워크플로를 이번 변경 범위 밖에서 건드리지
# 않기 위함이다(2026-07-16). 워커만 컨테이너화하는 이유: Docker를 켜도 워커가 같이
# 뜨지 않아 Redis만 살아있고 세션 후처리는 조용히 멈춰 있던 사고(2026-07-15)를
# 인프라 레벨에서 막기 위함 — docker-compose.yml의 restart: unless-stopped가
# 워커 프로세스 크래시 시 자동 재기동을 보장한다.
FROM python:3.13-slim

# WeasyPrint(app/services/pdf_service.py)는 셀러리 워커 부팅 시 즉시 임포트된다
# (app/workers/tasks.py가 모듈 최상단에서 pdf_service를 임포트하고, celery_app.py의
# include=["app.workers.tasks"]가 워커 시작 시 그 모듈을 즉시 로드한다) — PDF를 한
# 번도 안 만들어도 이 네이티브 라이브러리들이 없으면 워커 자체가 뜨지 못하고
# ImportError로 즉시 죽는다. requirements.txt의 WeasyPrint 주석(Windows GTK3 요구)과
# 동일한 이유의 Linux(Debian) 대응. libgdk-pixbuf-2.0-0(하이픈 위치 주의)는
# python:3.13-slim의 베이스가 Debian trixie로 넘어가며 패키지명이 바뀐 결과다 —
# 예전 이름 libgdk-pixbuf2.0-0으로 두면 "has no installation candidate"로 빌드가
# 깨진다(2026-07-16 재현).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# NLI 모델(MoritzLaurer/mDeBERTa-v3-base-mnli-xnli, app/clients/nli.py)의 HuggingFace
# 캐시 경로를 고정한다 — docker-compose.yml이 이 경로에 네임드 볼륨을 마운트해,
# restart_policy로 컨테이너가 재기동될 때마다(정상적인 재시작 포함) 수백MB를 다시
# 내려받지 않고 캐시를 재사용한다.
ENV HF_HOME=/app/.cache/huggingface

# Windows 호스트에서 직접 celery를 띄울 때는 멀티프로세싱 제약 때문에 --pool=solo가
# 필수였다(리눅스 컨테이너 안에서는 더 이상 해당 사항 없음). 다만 --pool=solo가 이미
# 이 프로젝트의 실사용 트래픽 규모에서 문제없이 검증된 설정이라, prefork로 바꿔
# NLI 모델(torch+transformers, 프로세스당 수백MB)을 워커 프로세스마다 중복 로드하는
# 새로운 미검증 리스크를 굳이 만들지 않고 그대로 유지한다 — 처리량이 실제 병목이
# 되면 그때 --pool=prefork --concurrency=N으로 바꾸고 메모리 여유를 확인하며
# 튜닝할 일이다.
CMD ["celery", "-A", "app.workers.celery_app", "worker", "--loglevel=info", "--pool=solo"]
