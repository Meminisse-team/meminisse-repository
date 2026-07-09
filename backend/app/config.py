from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Supabase PostgreSQL — Direct connection (마스터 권한, RLS 우회)
    # 형식: postgresql+asyncpg://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
    # Pooler(포트 6543) 미사용 — asyncpg prepared statement 캐시 충돌 방지
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/meminisse"

    # AWS S3 (미디어 원본 상시 보존)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_S3_BUCKET: str = "meminisse-media"
    AWS_REGION: str = "ap-northeast-2"

    # Upstage — Solar LLM + Document Parse + Embeddings 통합 키 (3종 API 모두 이 키 하나로 인증)
    # Solar:       AsyncOpenAI(base_url="https://api.upstage.ai/v1", api_key=...), model="solar-pro3"
    # Embeddings:  같은 클라이언트, model="embedding-query" | "embedding-passage"
    # Document Parse: POST https://api.upstage.ai/v1/document-digitization (multipart/form-data)
    UPSTAGE_API_KEY: str = ""

    # Celery + Redis (기획안 4절: 세션 후처리·최종 집필·PDF 조판 등 무거운 비동기 작업)
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # 프론트엔드(Next.js) 개발 서버 CORS 허용 origin
    CORS_ALLOW_ORIGINS: list[str] = ["http://localhost:3000"]


settings = Settings()
