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

    # Upstage — Solar LLM + Embeddings 통합 키 (2종 API 모두 이 키 하나로 인증)
    # Solar:       AsyncOpenAI(base_url="https://api.upstage.ai/v1", api_key=...), model="solar-pro3"
    # Embeddings:  같은 클라이언트, model="embedding-query" | "embedding-passage"
    UPSTAGE_API_KEY: str = ""

    # ── Azure Computer Vision (사진 캡셔닝 + 사진 속 텍스트 인식, Image Analysis 4.0) ──
    # 사진 한 장당 API 호출 1번으로 캡션(예: "집 앞에서 5명이 함께 찍은 사진")과 사진
    # 속 인쇄/손글씨 텍스트(예: "1990년 집 앞에서 가족들과.")를 동시에 받아온다
    # (app/clients/azure_vision.py, features=caption,read). 예전에는 텍스트 인식만
    # 가능한 Upstage Document Parse를 썼는데, 캡션 없이는 순수 추억 사진(글자가 없는
    # 사진)에 대해 의미 있는 시작 질문을 만들 수 없었다 — Azure Vision 한 번의 호출로
    # 캡션+텍스트를 함께 얻는 방식으로 교체했다(app/services/media_service.py 참조).
    #
    # 발급: https://portal.azure.com → "Computer Vision" 리소스 생성
    #   AZURE_CV_ENDPOINT → 리소스 개요의 "엔드포인트"
    #     (예: https://<resource-name>.cognitiveservices.azure.com)
    #   AZURE_CV_API_KEY  → 리소스의 "키 및 엔드포인트" → KEY 1 또는 KEY 2
    # 둘 다 비어 있으면(기본값) 사진 분석 자체를 건너뛰고 일반적인 오프닝 질문으로
    # 대체된다(AzureVisionNotConfiguredError, media_service._run_dual_track_analysis
    # 참조) — 앱이 죽지 않으므로 나중에 이 두 값만 채우면 별도 코드 수정 없이 바로
    # 동작한다.
    AZURE_CV_ENDPOINT: str = ""
    AZURE_CV_API_KEY: str = ""

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
