"""
Layer 1 검증 게이트 회귀 테스트.

기획안: "verified=false 데이터는 임베딩 및 RAG 적재 대상에서 제외된다." 이 테스트는
Mock 게이트웨이(EventGateway.search_verified)가 실제로 그 계약을 지키는지 확인한다.
Mock으로 검증하는 이유: 이 게이트는 인터페이스 계약의 일부이므로, 나중에 팀원의
Postgres/pgvector 구현체로 교체되어도 이 테스트가 그대로 그 구현체에 대해 재사용
가능해야 한다(같은 테스트를 SqlAlchemyEventGateway에 대해 돌리려면 실제 DB가
필요하므로, 이 스위트는 Mock 전용이고 실제 DB 통합 테스트는 별도로 필요하다).
"""

from __future__ import annotations

import uuid

import pytest

from app.gateways.dto import EventCreateData
from app.gateways.mock.gateways import MockEventGateway
from app.gateways.mock.store import MockStore
from app.models.enums import EventSourceType


def _make_event_data(*, user_id: uuid.UUID, verified: bool, embedding: list[float] | None) -> EventCreateData:
    return EventCreateData(
        user_id=user_id,
        source_type=EventSourceType.SESSION_CHAT,
        one_line_summary="테스트 사건",
        prose_paragraph="테스트 산문 문단",
        verified=verified,
        embedding=embedding,
    )


@pytest.mark.asyncio
async def test_search_verified_excludes_unverified_events() -> None:
    store = MockStore()
    gateway = MockEventGateway(store)
    user_id = uuid.uuid4()

    verified_events = await gateway.bulk_create(
        [_make_event_data(user_id=user_id, verified=True, embedding=[1.0, 0.0, 0.0])]
    )
    # 정상 경로: 미검증 이벤트는 embedding이 없다.
    await gateway.bulk_create(
        [_make_event_data(user_id=user_id, verified=False, embedding=None)]
    )
    # 방어적 케이스: 어떤 이유로든 미검증인데 embedding이 이미 존재해도 게이트가 막아야 한다.
    await gateway.bulk_create(
        [_make_event_data(user_id=user_id, verified=False, embedding=[1.0, 0.0, 0.0])]
    )

    results = await gateway.search_verified(
        user_id=user_id, query_embedding=[1.0, 0.0, 0.0], limit=10
    )

    assert [event.id for event in results] == [verified_events[0].id]
    assert all(event.verified for event in results)
    assert all(event.embedding is not None for event in results)


@pytest.mark.asyncio
async def test_search_verified_scopes_by_user() -> None:
    store = MockStore()
    gateway = MockEventGateway(store)
    owner_id, other_user_id = uuid.uuid4(), uuid.uuid4()

    await gateway.bulk_create(
        [_make_event_data(user_id=other_user_id, verified=True, embedding=[1.0, 0.0, 0.0])]
    )

    results = await gateway.search_verified(
        user_id=owner_id, query_embedding=[1.0, 0.0, 0.0], limit=10
    )

    assert results == []
