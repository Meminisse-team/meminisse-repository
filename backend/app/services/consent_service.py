"""
동의 기록(기획안 5절 동의 주체 분리, 6절 주의의무 이행 증빙).

자녀가 온보딩을 대신 세팅하더라도 데이터 수집·이용 동의는 정보주체(부모) 본인에게
첫 세션에서 직접 획득해야 한다. 이 서비스는 그 순간을 ConsentRecord로 남기는 얇은
레이어이며, 실제로 누구에게 어떤 화면에서 동의를 받을지는 프론트엔드/온보딩 플로우의
책임이다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentGrantedBy, ConsentRecord, ConsentType


async def record_consent(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    consent_type: ConsentType,
    notice_version: str,
    granted_by: ConsentGrantedBy,
) -> ConsentRecord:
    record = ConsentRecord(
        user_id=user_id,
        consent_type=consent_type,
        notice_version=notice_version,
        granted_by=granted_by,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def has_active_consent(db: AsyncSession, user_id: uuid.UUID, consent_type: ConsentType) -> bool:
    result = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.user_id == user_id,
            ConsentRecord.consent_type == consent_type,
            ConsentRecord.revoked_at.is_(None),
        )
    )
    return result.scalars().first() is not None


async def list_consents(db: AsyncSession, user_id: uuid.UUID) -> list[ConsentRecord]:
    result = await db.execute(
        select(ConsentRecord).where(ConsentRecord.user_id == user_id).order_by(ConsentRecord.granted_at.desc())
    )
    return list(result.scalars().all())
