"""
Azure AI Vision Image Analysis 4.0 클라이언트 — 사진 캡셔닝 + 사진 속 텍스트 인식.

예전에는 Upstage Document Parse(텍스트만 읽는 OCR)를 사진에도 그대로 썼는데, 글자가
없는 순수 추억 사진(예: 집 앞에서 찍은 가족사진)에 대해서는 아무 단서도 얻지 못해
"이 사진에 대해 더 자세히 이야기를 들려주시겠어요?" 같은 일반적인 질문만 던질 수
있었다. Azure Vision의 Image Analysis API는 캡션(caption, 사진의 시각적 내용을
자연어로 설명)과 텍스트 인식(read, 인쇄/손글씨 모두)을 `features=caption,read`
하나의 호출로 함께 지원하므로, API 호출 한 번으로 두 정보를 모두 얻어 Document
Parse를 완전히 대체한다(app/services/media_service.py 참조).

인증 안 됨(엔드포인트/키 미설정)은 예외(AzureVisionNotConfiguredError)로 구분해서
알린다 — 실제 Azure 리소스 없이도 로컬 개발이 막히지 않아야 하고(호출부가 이 예외를
잡아 사진 분석만 건너뛰고 업로드 자체는 정상 처리한다), 나중에 .env에 키만 채우면
코드 수정 없이 바로 동작해야 하기 때문이다.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings

API_VERSION = "2024-02-01"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class AzureVisionNotConfiguredError(Exception):
    """AZURE_CV_ENDPOINT/AZURE_CV_API_KEY가 아직 .env에 설정되지 않았다는 뜻.
    호출부는 이 예외를 실제 API 오류와 구분해 사진 분석만 건너뛰어야 한다."""


async def analyze_image(image_bytes: bytes, *, language: str = "ko") -> dict[str, Any]:
    """캡션 + 텍스트 인식을 한 번의 호출로 수행하고 Azure 원시 응답을 그대로 반환한다.

    응답 형태(Image Analysis 4.0):
      {"captionResult": {"text": "...", "confidence": 0.9},
       "readResult": {"blocks": [{"lines": [{"text": "..."}, ...]}, ...]}}
    caption/read 텍스트 추출은 이 클라이언트가 아니라 호출부(media_service.py)가
    한다 — document_parse.py가 원시 응답을 그대로 돌려주고 media_service.py가
    파싱하던 기존 관례와 동일하게 유지한다.
    """
    if not settings.AZURE_CV_ENDPOINT or not settings.AZURE_CV_API_KEY:
        raise AzureVisionNotConfiguredError(
            "AZURE_CV_ENDPOINT/AZURE_CV_API_KEY가 설정되지 않았습니다."
        )

    url = f"{settings.AZURE_CV_ENDPOINT.rstrip('/')}/computervision/imageanalysis:analyze"
    params = {
        "api-version": API_VERSION,
        "features": "caption,read",
        "language": language,
        "gender-neutral-caption": "true",
    }
    headers = {
        "Ocp-Apim-Subscription-Key": settings.AZURE_CV_API_KEY,
        "Content-Type": "application/octet-stream",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(url, params=params, headers=headers, content=image_bytes)
        response.raise_for_status()
        return response.json()


def extract_caption(analysis: dict[str, Any]) -> str | None:
    """analyze_image() 원시 응답에서 캡션 문장만 뽑아낸다. 없으면 None."""
    caption = ((analysis.get("captionResult") or {}).get("text") or "").strip()
    return caption or None


def extract_read_text(analysis: dict[str, Any]) -> str | None:
    """analyze_image() 원시 응답에서 인식된 모든 텍스트 줄을 순서대로 이어붙인다.
    사진 속 여러 줄의 손글씨/인쇄 메모를 하나의 문자열로 합쳐, 기존 OCR 텍스트
    처리 로직(시기 추정 등)에 그대로 넘길 수 있게 한다. 텍스트가 전혀 없으면 None."""
    blocks = (analysis.get("readResult") or {}).get("blocks") or []
    lines = [
        line.get("text", "").strip()
        for block in blocks
        for line in block.get("lines") or []
        if line.get("text")
    ]
    joined = "\n".join(line for line in lines if line)
    return joined or None
