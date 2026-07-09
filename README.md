# meminisse-repository

초고령화 사회를 위한 AI 자서전 대필 에이전트 서비스 'Meminisse'.

## 구조

```
backend/    FastAPI + SQLAlchemy(async) + Celery
frontend/   Next.js (App Router)
```

## Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # 값 채우기 (DATABASE_URL, UPSTAGE_API_KEY, AWS_*, CELERY_*)
alembic upgrade head
uvicorn app.main:app --reload
```

Celery 워커(세션 후처리 등 비동기 작업)를 별도로 띄우려면:

```bash
cd backend
celery -A app.workers.celery_app worker --loglevel=info
```

## Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local  # NEXT_PUBLIC_API_BASE_URL
npm run dev
```
