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


async def reconcile_stale_sessions(gateways: Gateways) -> int:
    """처리 지연 세션(list_stale_sessions과 동일한 기준)을 찾아 Phase 2 후처리를
    다시 큐잉한다 — Celery Beat가 주기적으로 호출하는 자동 복구 태스크다
    (app/workers/tasks.py:reconcile_stale_sessions, app/workers/celery_app.py의
    beat_schedule). "나의 이야기" 산문이 큐잉 실패(브로커 순간 다운 등)로 영구
    유실되던 사고(2026-07-15)를 사람 개입 없이 스스로 복구하기 위한 2차
    방어선이다 — 1차 방어선은 큐잉 시점의 즉시 재시도(app/workers/enqueue.py).
    사람이 개인 서사 데이터를 열람하는 게 아니라 세션 ID만 다루는 자동화된
    시스템 동작이라 감사 로그는 남기지 않는다(admin_audit_logs는 관리자의
    콘텐츠 열람만 추적한다).

    반환값은 이번 실행에서 재큐잉을 시도한 세션 개수 — Celery 태스크가 로그로
    남긴다."""
    threshold = datetime.now(timezone.utc) - _STALE_THRESHOLD
    stale = await gateways.sessions.list_stale_completed(older_than=threshold)
    if not stale:
        return 0

    from app.workers.enqueue import enqueue_with_retry
    from app.workers.tasks import process_session_completion  # 순환 임포트 방지용 지연 임포트

    for session in stale:
        await enqueue_with_retry(
            process_session_completion,
            str(session.id),
            log_context=f"session_id={session.id} (reconcile)",
        )
    return len(stale)
