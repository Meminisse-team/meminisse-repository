from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 게이트웨이 계층 DI 스위치 (app/gateways/factory.py 참조)
    #   mock:     인메모리 Mock 게이트웨이. DB/S3 없이 로컬 실행·데모·테스트 전용.
    #   postgres: 현재는 SQLAlchemy(Supabase) 구현체. 팀원의 Postgres/S3 연동 완성 후
    #             이 값 자체는 그대로 두고 factory.py의 구현체 임포트만 교체하면 된다.
    GATEWAY_BACKEND: Literal["mock", "postgres"] = "postgres"

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

    # ── 인증 (Supabase Auth) ────────────────────────────────────────────────────
    # 자체 비밀번호 해싱/JWT 발급 대신, 이 Supabase 프로젝트에 이미 프로비저닝되어
    # 있는 인증 서비스(auth 스키마, GoTrue)를 그대로 쓴다(app/clients/supabase_auth.py,
    # app/core/security.py). 이메일 인증·비밀번호 재설정·소셜 로그인을 자체 구현할
    # 필요가 없고, 비밀번호 관련 값을 이 프로젝트 DB에 전혀 저장하지 않는다.
    # 전부 Supabase Dashboard → Settings → API에서 확인 가능:
    #   SUPABASE_URL              → "Project URL" (예: https://xxxx.supabase.co)
    #   SUPABASE_ANON_KEY         → "anon public" 키. 로그인/토큰 갱신에 사용.
    #                                공개돼도 되는 키(프론트엔드에도 노출 가능)이지만,
    #                                이 프로젝트는 백엔드가 대신 호출하므로 .env에만 둔다.
    #   SUPABASE_SERVICE_ROLE_KEY → "service_role" 키. 회원가입 시 관리자 권한으로
    #                                이메일 인증 절차 없이 즉시 계정을 만드는 데 사용.
    #                                RLS를 완전히 우회하는 매우 민감한 키 —
    #                                절대 프론트엔드/커밋에 노출하지 말 것.
    #   SUPABASE_JWT_SECRET       → Settings → API → JWT Settings → "JWT Secret".
    #                                세션 토큰(HS256) 서명 검증에 사용. 프로젝트가
    #                                비대칭키(RS256/ES256) 서명으로 전환돼 있으면
    #                                이 방식 대신 JWKS 엔드포인트 검증으로 바꿔야 한다.
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""


settings = Settings()
