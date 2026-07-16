from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import APIStatusError

from app.api.v1.router import api_router
from app.config import settings
from app.core.logging_config import configure_file_logging

configure_file_logging("backend")

app = FastAPI(title="Meminisse API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(APIStatusError)
async def upstage_api_error_handler(request: Request, exc: APIStatusError) -> JSONResponse:
    """Upstage(OpenAI SDK 호환) 호출 실패를 원본 트레이스백 대신 정리된 형태로 반환한다.

    /sandbox/* 엔드포인트가 실제 API를 직접 호출하는 동안 가장 자주 마주치는 오류
    (잘못된 UPSTAGE_API_KEY, 요금 초과, 모델/파라미터 비호환 등)를 Swagger에서 바로
    읽을 수 있게 하기 위함이다. solar_service를 호출하는 다른 라우터에도 동일하게 적용된다.
    """
    return JSONResponse(status_code=exc.status_code, content={"detail": f"Upstage API 오류: {exc}"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
