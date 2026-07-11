"""
이벤트(사건) 조회 전용 서비스. event_extraction_service.py는 Phase 2 후처리 파이프라인
(산문 재조립 → 이벤트 분할·라벨 추출)을 다루는 쓰기 경로 전담이라, 순수 조회 책임까지
그 파일에 얹으면 관심사가 섞인다 — 그래서 이 모듈을 따로 뒀다.
"""

from __future__ import annotations

import uuid

from app.gateways.dto import EventRecord
from app.gateways.factory import Gateways


async def list_events(gateways: Gateways, user_id: uuid.UUID) -> list[EventRecord]:
    """GET /events(나의 이야기 탭). verified=True이고 Phase 3 병합으로 흡수되지
    않은 사건만 created_at 내림차순으로 반환한다(EventGateway.list_for_timeline —
    Layer 1 검증 게이트가 게이트웨이 구현체 내부에서 항상 강제됨, app/gateways/
    interfaces.py 참조). OCR 오인식 의심으로 격리된(verified=False) 사건이나
    아직 확인 질문을 거치지 않은 사건은 이 목록에 나타나지 않는다 — 사용자가
    확인하지 않은 내용을 "내 이야기"로 보여주지 않기 위함이다."""
    return await gateways.events.list_for_timeline(user_id)
