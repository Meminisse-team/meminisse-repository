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
    """S3에 업로드하고 공개 접근 가능한 URL을 반환한다."""
    await asyncio.to_thread(
        _client().put_object,
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
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
