"""
Phase 3(이벤트 병합·중요도 산정·스타일 바이블)과 Phase 4(동적 목차·하향식 집필·
팩트체크·제3자 위해성 분류)의 오케스트레이션은 이벤트 병합 판정 전략, 중요도
가중치 산정, RAG 검색 파라미터 등 별도 설계 논의가 필요한 큰 작업이라 이번
스켈레톤에는 포함하지 않았다. app/agents/prompts.py에 해당 단계의 프롬프트
(스타일 바이블, 병합 판정, 목차 생성, 시놉시스/챕터 집필, 통일성 윤문, 팩트체크,
제3자 위해성 분류)는 이미 준비되어 있으므로, 다음 단계에서 이 서비스 파일에
Phase 3/4 함수를 채워 넣으면 된다.
"""

from __future__ import annotations

import uuid

from app.gateways.dto import AutobiographyRecord
from app.gateways.factory import Gateways


async def get_or_create_autobiography(gateways: Gateways, user_id: uuid.UUID) -> AutobiographyRecord:
    autobiography = await gateways.autobiographies.get_by_user_id(user_id)
    if autobiography is not None:
        return autobiography

    autobiography = await gateways.autobiographies.create(user_id)
    await gateways.commit()
    return autobiography
