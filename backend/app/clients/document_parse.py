"""
Upstage Document Parse (기획안 상 "Document Parse API") 클라이언트.

기획안 4절 Phase 1: 업로드된 일기장/편지/메모 등에서 텍스트와 레이아웃을 추출해
Solar 1차 타당성 검증 → Event(source_type=DOCUMENT, verified=false)로 적재한다.

엔드포인트 정정: 레거시 .env.example/config.py 주석은 `/v1/document-ai/document-parse`
였으나, 실제 upstage_document_parse_api_docs.txt 기준 정확한 엔드포인트는
`/v1/document-digitization`(동기, 최대 100페이지)이다. 100페이지를 넘는 대용량
문서는 `/v1/document-digitization/async` + 폴링을 사용해야 한다(최대 1,000페이지,
10페이지 단위 배치, 큐 적체 시 최대 72시간 대기 가능 — Celery 워커에서만 호출할 것).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings

BASE_URL = "https://api.upstage.ai/v1/document-digitization"
# elements[].content.text에서 OCR이 인식하지 못한 문자를 표시하는 마커. Solar 1차
# 타당성 검증(오인식 의심 구간 탐지) 전에 이 마커의 존재만으로 1차 스크리닝 가능.
UNKNOWN_CHAR_MARKER = "�"  # "�"

# 동기 API는 서버 사이드 5분 타임아웃이 있다(공식 문서). 여유를 두고 클라이언트 타임아웃 설정.
_SYNC_TIMEOUT = httpx.Timeout(280.0, connect=10.0)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.UPSTAGE_API_KEY}"}


async def parse_document_sync(
    file_bytes: bytes,
    filename: str,
    *,
    model: str = "document-parse",
    mode: str = "standard",
    output_formats: list[str] | None = None,
    ocr: str = "auto",
) -> dict[str, Any]:
    """최대 100페이지 문서를 동기 처리한다. 초과분은 앞 100페이지만 처리됨(공식 문서)."""
    data = {
        "model": model,
        "mode": mode,
        "ocr": ocr,
        "output_formats": _json_array(output_formats or ["markdown"]),
    }
    async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as client:
        response = await client.post(
            BASE_URL,
            headers=_auth_headers(),
            files={"document": (filename, file_bytes)},
            data=data,
        )
        response.raise_for_status()
        return response.json()


async def submit_async_parse(
    file_bytes: bytes,
    filename: str,
    *,
    model: str = "document-parse",
) -> str:
    """최대 1,000페이지 문서를 비동기 큐에 제출하고 request_id를 반환한다."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(
            f"{BASE_URL}/async",
            headers=_auth_headers(),
            files={"document": (filename, file_bytes)},
            data={"model": model},
        )
        response.raise_for_status()
        return response.json()["request_id"]


async def get_async_parse_status(request_id: str) -> dict[str, Any]:
    """status: submitted → started → completed | failed. batches[].download_url은 15분 유효."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(
            f"{BASE_URL}/requests/{request_id}",
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return response.json()


def _json_array(values: list[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"
