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

    # Upstage — Solar LLM + Document Parse 통합 키
    # Solar: AsyncOpenAI(base_url="https://api.upstage.ai/v1/solar", api_key=...)
    # Document Parse: POST https://api.upstage.ai/v1/document-ai/document-parse
    UPSTAGE_API_KEY: str = ""

    # OpenAI — text-embedding-3-large (1536차원) 전용
    # Solar LLM과 별개로 임베딩만 OpenAI 사용
    OPENAI_API_KEY: str = ""


settings = Settings()
