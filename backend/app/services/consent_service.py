"""
동의 기록(기획안 5절 동의 주체 분리, 6절 주의의무 이행 증빙).

자녀가 온보딩을 대신 세팅하더라도 데이터 수집·이용 동의는 정보주체(부모) 본인에게
첫 세션에서 직접 획득해야 한다. 이 서비스는 그 순간을 ConsentGrant로 남기는 얇은
레이어이며, 실제로 누구에게 어떤 화면에서 동의를 받을지는 프론트엔드/온보딩 플로우의
책임이다.
"""

from __future__ import annotations

import uuid

from app.gateways.dto import ConsentGrant, ConsentGrantCreateData
from app.gateways.factory import Gateways
from app.models.enums import ConsentGrantedBy, ConsentType


async def record_consent(
    gateways: Gateways,
    user_id: uuid.UUID,
    *,
    consent_type: ConsentType,
    notice_version: str,
    granted_by: ConsentGrantedBy,
) -> ConsentGrant:
    record = await gateways.consents.create(
        ConsentGrantCreateData(
            user_id=user_id,
            consent_type=consent_type,
            notice_version=notice_version,
            granted_by=granted_by,
        )
    )
    await gateways.commit()
    return record


async def has_active_consent(gateways: Gateways, user_id: uuid.UUID, consent_type: ConsentType) -> bool:
    return await gateways.consents.has_active(user_id, consent_type)


async def list_consents(gateways: Gateways, user_id: uuid.UUID) -> list[ConsentGrant]:
    return await gateways.consents.list_by_user(user_id)
