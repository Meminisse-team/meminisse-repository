"""관리자 대시보드 서비스: 파이프라인 상태(처리 지연 세션)와 위기 대응 로그 조회.

2026-07-15 실사용 중 Docker/Celery 워커가 다운된 동안 완료된 세션 6개가 처리
대기 상태로 20분 넘게 방치된 사고를 계기로 도입한다 — 관리자가 DB를 직접
조회하지 않고도 이런 상태를 즉시 발견할 수 있어야 한다. 조회 자체가 사용자의
개인 서사 데이터(대화·산문)에 접근하는 행위이므로, 매 조회마다
admin_audit_logs에 최소 기록을 남긴다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.agents import prompts
from app.gateways.dto import AdminAuditLogCreateData, InterviewSessionRecord
from app.gateways.factory import Gateways

# 이보다 오래 COMPLETED인데 session_prose가 없으면 "처리 지연"으로 간주한다.
# 정상 처리는 보통 수십 초~2분 내 끝나므로(2026-07-15 실사용 처리 시간 참조,
# 예: 87.72초) 오탐(정상 처리 중인 세션을 지연으로 잘못 표시)을 줄이기 위해
# 여유를 둔다.
_STALE_THRESHOLD = timedelta(minutes=10)


async def list_stale_sessions(
    gateways: Gateways, *, admin_id: uuid.UUID
) -> list[InterviewSessionRecord]:
    threshold = datetime.now(timezone.utc) - _STALE_THRESHOLD
    sessions = await gateways.sessions.list_stale_completed(older_than=threshold)
    await gateways.audit.record(
        AdminAuditLogCreateData(admin_id=admin_id, action="view_stale_sessions")
    )
    await gateways.commit()
    return sessions


async def list_crisis_sessions(
    gateways: Gateways, *, admin_id: uuid.UUID
) -> list[InterviewSessionRecord]:
    sessions = await gateways.sessions.list_by_chat_log_content(prompts.TIER2_CRISIS_RESPONSE)
    await gateways.audit.record(
        AdminAuditLogCreateData(admin_id=admin_id, action="view_crisis_sessions")
    )
    await gateways.commit()
    return sessions
