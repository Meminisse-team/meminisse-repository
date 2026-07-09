"""
S3 기반 ObjectStorageGateway 구현체. boto3 호출 자체는 app/clients/s3.py(얇은 API
래퍼)에 그대로 두고, 이 클래스는 그 함수들을 인터페이스 계약에 맞게 감싸기만 한다.
팀원이 자체 S3 연동(예: 리전별 버킷 분리, 멀티파트 업로드 등)을 가져오면 이 클래스
내부만 교체하거나, 이 클래스 자체를 팀원 구현으로 바꿔치기하면 된다.
"""

from __future__ import annotations

from app.clients import s3
from app.gateways.interfaces import ObjectStorageGateway


class S3ObjectStorageGateway(ObjectStorageGateway):
    async def put_object(self, key: str, data: bytes, *, content_type: str) -> str:
        return await s3.upload_bytes(key, data, content_type=content_type)

    async def get_presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        return await s3.generate_presigned_get_url(key, expires_in=expires_in)
