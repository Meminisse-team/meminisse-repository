"""
Azure AI Vision Image Analysis 4.0 클라이언트 — 물체 탐지·장면 태그·사진 속 텍스트 인식.

예전에는 Upstage Document Parse(텍스트만 읽는 OCR)를 사진에도 그대로 썼는데, 글자가
없는 순수 추억 사진(예: 집 앞에서 찍은 가족사진)에 대해서는 아무 단서도 얻지 못해
"이 사진에 대해 더 자세히 이야기를 들려주시겠어요?" 같은 일반적인 질문만 던질 수
있었다. Azure Vision의 Image Analysis API는 물체 탐지(objects)·장면 태그(tags)·
텍스트 인식(read)을 `features=objects,tags,read` 한 번의 호출로 함께 지원하므로,
API 호출 한 번으로 사진 내용에 대한 단서를 모두 얻어 Document Parse를 완전히
대체한다(app/services/media_service.py 참조).

**Caption(자연어 한 문장 요약) 기능은 의도적으로 쓰지 않는다** — Image Analysis
4.0의 caption/dense-captions는 (1) 일부 지역(East US·West Europe·North Europe·
France Central·Southeast Asia·East Asia·Korea Central 등)에서만 지원되고, (2) 그
지역에서도 caption 문장 자체는 영어로만 생성된다(비영어 language 파라미터를 주면
NotSupportedLanguage로 거부됨) — 이 두 제약을 실제로 여러 지역에서 재현·확인했다
(2026-07-16). 반면 objects/tags/read는 이런 지역·언어 제약이 없어 훨씬 넓은 지역에서
안정적으로 동작한다(Japan East에서 실제 재현·확인). 다만 tags/objects의 라벨 이름
자체는 영어로 나오므로, 한국어 질문 문구로 다듬는 건 이 클라이언트가 아니라 호출부가
Solar로 처리한다(app/agents/prompts.py:build_scene_description_prompt).

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

# tags는 관련성 낮은 항목까지 광범위하게 반환하는 경향이 있어(confidence 0.3대도
# 흔함), 오프닝 질문 재료로 쓰기엔 너무 산만해진다 — 이 값 이상만 채택한다.
_TAG_MIN_CONFIDENCE = 0.5


class AzureVisionNotConfiguredError(Exception):
    """AZURE_CV_ENDPOINT/AZURE_CV_API_KEY가 아직 .env에 설정되지 않았다는 뜻.
    호출부는 이 예외를 실제 API 오류와 구분해 사진 분석만 건너뛰어야 한다."""


async def analyze_image(image_bytes: bytes, *, language: str | None = None) -> dict[str, Any]:
    """물체 탐지 + 장면 태그 + 텍스트 인식을 한 번의 호출로 수행하고 Azure 원시
    응답을 그대로 반환한다.

    응답 형태(Image Analysis 4.0):
      {"objectsResult": {"values": [{"tags": [{"name": "...", "confidence": 0.9}], ...}]},
       "tagsResult": {"values": [{"name": "...", "confidence": 0.9}, ...]},
       "readResult": {"blocks": [{"lines": [{"text": "..."}, ...]}, ...]}}
    language를 굳이 넘기지 않는 이유: objects/tags/read 조합에서도 "ko" 등 비영어
    값을 주면 NotSupportedLanguage로 거부되는 걸 재현·확인했다(2026-07-16) — 아예
    생략하면(기본 영어) 정상 동작한다. 결과 파싱은 이 클라이언트가 아니라
    호출부(media_service.py)가 한다 — document_parse.py가 원시 응답을 그대로
    돌려주고 media_service.py가 파싱하던 기존 관례와 동일하게 유지한다.
    """
    if not settings.AZURE_CV_ENDPOINT or not settings.AZURE_CV_API_KEY:
        raise AzureVisionNotConfiguredError(
            "AZURE_CV_ENDPOINT/AZURE_CV_API_KEY가 설정되지 않았습니다."
        )

    url = f"{settings.AZURE_CV_ENDPOINT.rstrip('/')}/computervision/imageanalysis:analyze"
    params: dict[str, Any] = {
        "api-version": API_VERSION,
        "features": "objects,tags,read",
    }
    if language is not None:
        params["language"] = language
    headers = {
        "Ocp-Apim-Subscription-Key": settings.AZURE_CV_API_KEY,
        "Content-Type": "application/octet-stream",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(url, params=params, headers=headers, content=image_bytes)
        response.raise_for_status()
        return response.json()


def extract_objects(analysis: dict[str, Any]) -> list[str]:
    """analyze_image() 원시 응답에서 탐지된 물체 이름만(영어) 중복 없이 뽑는다.
    각 detection은 boundingBox + tags(보통 물체 이름 하나)로 온다 — 사진 속 위치
    정보(boundingBox)는 오프닝 질문 재료로는 쓰지 않으므로 여기서 버린다.

    같은 이름이 여러 번 감지돼도(예: 사람 2명) 중복 제거하지 않고 그대로
    반환한다 — 인원수 같은 정보가 사라지지 않게 하기 위함이다(실사용 사진으로
    검증 중, 아이 두 명이 있는 사진에서 이름만 남기고 중복 제거했더니 최종
    한국어 설명이 "아이" 한 명으로 뭉개지는 문제를 재현·확인했다, 2026-07-16).
    개수 집계는 호출부(build_scene_description_prompt)가 한다."""
    values = (analysis.get("objectsResult") or {}).get("values") or []
    names: list[str] = []
    for item in values:
        tags = item.get("tags") or []
        if tags and tags[0].get("name"):
            names.append(tags[0]["name"])
    return names


def extract_tags(analysis: dict[str, Any], *, min_confidence: float = _TAG_MIN_CONFIDENCE) -> list[str]:
    """analyze_image() 원시 응답에서 confidence가 임계값 이상인 장면 태그 이름만
    (영어) confidence 내림차순으로 뽑는다."""
    values = (analysis.get("tagsResult") or {}).get("values") or []
    filtered = [v for v in values if (v.get("confidence") or 0) >= min_confidence]
    filtered.sort(key=lambda v: v.get("confidence") or 0, reverse=True)
    return [v["name"] for v in filtered if v.get("name")]


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
