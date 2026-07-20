"""
AWS S3 클라이언트 (Layer 0 불변 원천 저장소: 업로드 이미지 원본, 대화 로그 원문).

boto3는 동기 라이브러리이므로 FastAPI 비동기 경로에서는 asyncio.to_thread로 감싸
이벤트 루프를 막지 않는다. 무거운 업로드는 Celery 워커에서 처리되는 경우가 많아
실제로는 스레드 오프로딩이 필요 없을 수도 있으나, API 서버에서 직접 호출하는
경로(예: 사진 즉시 업로드)를 대비해 기본을 안전하게 유지한다.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

import boto3

from app.config import settings


@lru_cache(maxsize=1)
def _client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )


async def upload_bytes(key: str, data: bytes, *, content_type: str) -> str:
    """S3에 업로드하고 공개 접근 가능한 URL을 반환한다.

    CacheControl을 명시하지 않으면 브라우저가 기본 휴리스틱으로 이 URL의
    응답을 자유롭게 캐싱할 수 있다 — 같은 key(예: 자서전 PDF,
    users/{user_id}/manuscripts/{autobiography_id}.pdf)를 재생성해 덮어써도,
    이미 한 번 받은 브라우저는 재검증 없이 예전에 캐싱해 둔 버전을 계속
    내려받는 문제가 실사용 중 발견됐다(2026-07-20 — 챕터 수정 후 PDF를
    다시 만들었는데 다운로드는 계속 옛 버전으로 되는 사고). no-cache는
    "아예 캐싱 금지"가 아니라 "쓰기 전에 항상 서버(S3)에 재검증하라"는
    뜻이라, ETag가 같으면(내용이 실제로 같으면) 304로 응답해 대역폭도
    아낀다 — 이 프로젝트의 모든 업로드(사진 원본 포함)가 나중에 재생성·
    교체될 수 있는 key를 쓰므로 전역으로 적용한다."""
    await asyncio.to_thread(
        _client().put_object,
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl="no-cache, must-revalidate",
    )
    return f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"


async def generate_presigned_get_url(key: str, *, expires_in: int = 3600) -> str:
    return await asyncio.to_thread(
        _client().generate_presigned_url,
        "get_object",
        Params={"Bucket": settings.AWS_S3_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


async def download_bytes(key: str) -> bytes:
    def _get() -> bytes:
        response = _client().get_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
        return response["Body"].read()

    return await asyncio.to_thread(_get)
