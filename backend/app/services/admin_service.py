"""관리자 대시보드 서비스: 파이프라인 상태(처리 지연 세션)와 위기 대응 로그 조회.

2026-07-15 실사용 중 Docker/Celery 워커가 다운된 동안 완료된 세션 6개가 처리
대기 상태로 20분 넘게 방치된 사고를 계기로 도입한다 — 관리자가 DB를 직접
조회하지 않고도 이런 상태를 즉시 발견할 수 있어야 한다. 조회 자체가 사용자의
개인 서사 데이터(대화·산문)에 접근하는 행위이므로, 매 조회마다
admin_audit_logs에 최소 기록을 남긴다.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agents import prompts
from app.clients import supabase_auth
from app.gateways.dto import (
    AdminAuditLogCreateData,
    AdminAuditLogRecord,
    AutobiographyRecord,
    ChapterDraftRecord,
    EventRecord,
    InterviewSessionRecord,
    UserRecord,
)
from app.gateways.factory import Gateways
from app.services import event_extraction_service

# 이보다 오래 COMPLETED인데 session_prose가 없으면 "처리 지연"으로 간주한다.
# 정상 처리는 보통 수십 초~2분 내 끝나므로(2026-07-15 실사용 처리 시간 참조,
# 예: 87.72초) 오탐(정상 처리 중인 세션을 지연으로 잘못 표시)을 줄이기 위해
# 여유를 둔다.
_STALE_THRESHOLD = timedelta(minutes=10)

# 관리자 대시보드 로그 화면이 읽는 로그 파일 위치. app/core/logging_config.py가
# 같은 경로(backend/logs/)에 기록한다 — 둘 다 backend/ 기준 상대 경로를 이
# 파일 위치에서 역산해 구한다(parents[2] = backend/).
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


class AdminUserNotFoundError(Exception):
    """조회/조작하려는 유저가 존재하지 않는다."""


class AdminSessionNotFoundError(Exception):
    """세션이 없거나 지정한 유저의 소유가 아니다."""


class AdminProseNotReadyError(Exception):
    """아직 Phase 2 후처리(산문 재조립)가 끝나지 않아 편집할 산문이 없다."""


class AdminEmailAlreadyRegisteredError(Exception):
    """다른 계정이 이미 쓰고 있는 이메일이다."""


class AdminAutobiographyNotFoundError(Exception):
    """자서전이 없거나 지정한 유저의 소유가 아니다."""


class AdminPdfNotReadyError(Exception):
    """최종본(윤문)이 아직 없어 PDF를 조판할 수 없다."""


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

    from app.workers.enqueue import enqueue_session_phase2_processing

    # 세션 단위 락(app/workers/enqueue.py 모듈 참조)이 이미 걸려 있는 세션은
    # 건너뛴다 — "완료됐는데 session_prose가 아직 없다"는 이 함수의 판정 기준
    # 하나만으로는 "메시지가 진짜 유실됨"과 "이미 큐에서 순서를 기다리는 중"을
    # 구분할 수 없어, 워커가 바쁠 때(챕터 집필 백로그 등) 같은 세션을 5분마다
    # 무한정 재큐잉하던 문제의 수정(2026-07-19, 378개까지 중복 누적됐던 사고
    # 참조). 반환값(재큐잉을 "시도"한 세션 수)은 실제로 큐잉된 것만 센다 —
    # 락에 걸려 건너뛴 세션은 이미 처리 중이므로 세지 않는 게 이 지표의 원래
    # 목적("이번에 실제로 개입한 세션 수")에 더 맞는다.
    requeued = 0
    for session in stale:
        enqueued = await enqueue_session_phase2_processing(
            session.id, log_context=f"session_id={session.id} (reconcile)"
        )
        if enqueued:
            requeued += 1
    return requeued


async def lookup_user(
    gateways: Gateways, *, admin_id: uuid.UUID, identifier: str
) -> tuple[UserRecord, list[InterviewSessionRecord]] | None:
    """identifier가 UUID로 파싱되면 id로, 아니면 이메일(exact match)로 조회한다.
    찾으면 이 유저의 세션 전체(list_by_user, session_prose 포함)도 함께 반환한다
    — 관리자가 유저 액세스 화면에서 프로필과 세션을 한 번에 보기 위함."""
    try:
        user_id = uuid.UUID(identifier)
    except ValueError:
        user = await gateways.users.get_by_email(identifier)
    else:
        user = await gateways.users.get_by_id(user_id)

    if user is None:
        return None

    sessions = await gateways.sessions.list_by_user(user.id)
    await gateways.audit.record(
        AdminAuditLogCreateData(admin_id=admin_id, action="view_user_detail", target_user_id=user.id)
    )
    await gateways.commit()
    return user, sessions


async def update_user_session_prose(
    gateways: Gateways,
    *,
    admin_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    new_prose: str,
) -> InterviewSessionRecord:
    """관리자가 유저 대신 산문을 고친다. story_service.update_session_prose와
    동일한 편집 경로(apply_user_prose_edit → 이벤트 재추출)를 재사용하지만,
    일반 유저용 60초 쿨다운(story_service._PROSE_EDIT_COOLDOWN)은 적용하지
    않는다 — 관리자는 문제를 발견한 즉시 여러 번 고쳐야 할 수 있다."""
    session = await gateways.sessions.get_by_id(session_id)
    if session is None or session.user_id != user_id:
        raise AdminSessionNotFoundError()
    if session.session_prose is None:
        raise AdminProseNotReadyError()

    now = datetime.now(timezone.utc)
    await gateways.sessions.apply_user_prose_edit(session_id, new_prose=new_prose, edited_at=now)
    await gateways.commit()
    await event_extraction_service.reextract_events_from_edited_prose(gateways, session_id)

    await gateways.audit.record(
        AdminAuditLogCreateData(
            admin_id=admin_id,
            action="edit_user_session_prose",
            target_user_id=user_id,
            target_session_id=session_id,
        )
    )
    await gateways.commit()

    updated = await gateways.sessions.get_by_id(session_id)
    assert updated is not None
    return updated


async def update_user_email(
    gateways: Gateways, *, admin_id: uuid.UUID, user_id: uuid.UUID, new_email: str
) -> UserRecord:
    """로그인 이메일은 Supabase Auth가 진실의 원천이므로 그쪽을 먼저 바꾸고,
    성공했을 때만 이 앱의 users 테이블(미러)을 갱신한다 — Supabase 호출이
    실패하면 로컬 DB는 전혀 건드리지 않아 두 저장소가 어긋나지 않는다."""
    user = await gateways.users.get_by_id(user_id)
    if user is None:
        raise AdminUserNotFoundError()

    existing = await gateways.users.get_by_email(new_email)
    if existing is not None and existing.id != user_id:
        raise AdminEmailAlreadyRegisteredError()

    await supabase_auth.admin_update_user(user_id, email=new_email)
    updated = await gateways.users.update_email(user_id, new_email)

    await gateways.audit.record(
        AdminAuditLogCreateData(admin_id=admin_id, action="admin_update_user_email", target_user_id=user_id)
    )
    await gateways.commit()
    return updated


async def reset_user_password(
    gateways: Gateways, *, admin_id: uuid.UUID, user_id: uuid.UUID, new_password: str
) -> None:
    """비밀번호 값 자체는 어디에도 저장/기록하지 않는다 — Supabase Auth에만
    전달되고, 감사 로그에는 이 동작이 일어났다는 사실(action, target_user_id)만
    남는다."""
    user = await gateways.users.get_by_id(user_id)
    if user is None:
        raise AdminUserNotFoundError()

    await supabase_auth.admin_update_user(user_id, password=new_password)

    await gateways.audit.record(
        AdminAuditLogCreateData(
            admin_id=admin_id, action="admin_reset_user_password", target_user_id=user_id
        )
    )
    await gateways.commit()


async def list_user_autobiographies(
    gateways: Gateways, *, admin_id: uuid.UUID, user_id: uuid.UUID
) -> list[AutobiographyRecord]:
    """이 유저가 완성한(final_content 존재) 자서전 전체 — 실물 출판용 PDF를
    관리자가 대신 내려받는 화면 전용(고객 자신은 "나의 책장"에서 본다)."""
    autobiographies = await gateways.autobiographies.list_finished_by_user(user_id)
    await gateways.audit.record(
        AdminAuditLogCreateData(
            admin_id=admin_id, action="view_user_autobiographies", target_user_id=user_id
        )
    )
    await gateways.commit()
    return autobiographies


async def trigger_autobiography_pdf(
    gateways: Gateways, *, admin_id: uuid.UUID, user_id: uuid.UUID, autobiography_id: uuid.UUID
) -> None:
    """관리자가 고객 소유의 자서전 PDF 조판을 대신 트리거한다(실물 인쇄 준비용).
    일반 유저용 엔드포인트(app/api/v1/autobiographies.py:generate_pdf)는 소유권을
    current_user와 비교해 검증하는데, 관리자는 그 자서전의 소유자가 아니므로
    별도 경로가 필요하다 — 대신 여기서 autobiography.user_id == user_id로
    검증해 다른 유저의 자서전을 경로 파라미터 조작만으로 조판하지 못하게 막는다."""
    autobiography = await gateways.autobiographies.get_by_id(autobiography_id)
    if autobiography is None or autobiography.user_id != user_id:
        raise AdminAutobiographyNotFoundError()
    if not autobiography.final_content:
        raise AdminPdfNotReadyError()

    from app.workers.enqueue import enqueue_generate_manuscript_pdf

    # 유저용 엔드포인트와 동일한 중복 재큐잉 방지 락을 거친다(2026-07-19) —
    # 안 그러면 고객이 방금 누른 요청과 관리자가 대신 누른 요청이 동시에 큐에
    # 쌓일 수 있다.
    await enqueue_generate_manuscript_pdf(autobiography_id, log_context=f"admin_id={admin_id}")

    await gateways.audit.record(
        AdminAuditLogCreateData(
            admin_id=admin_id,
            action="admin_trigger_pdf_generate",
            target_user_id=user_id,
        )
    )
    await gateways.commit()


async def list_db_table(
    gateways: Gateways, *, admin_id: uuid.UUID, table: str, limit: int, offset: int
) -> list[UserRecord] | list[InterviewSessionRecord] | list[EventRecord] | list[AutobiographyRecord] | list[ChapterDraftRecord]:
    """DB 열람 화면 전용 — 화이트리스트된 5개 도메인 모델만 페이지네이션 조회한다
    (임의 SQL 없음). table 값 자체의 유효성은 라우터가 AdminDbTable enum으로
    먼저 검증하므로, 여기서 매칭되지 않는 값은 방어적으로만 다룬다."""
    match table:
        case "users":
            rows = await gateways.users.list_all(limit=limit, offset=offset)
        case "sessions":
            rows = await gateways.sessions.list_all(limit=limit, offset=offset)
        case "events":
            events = await gateways.events.list_all(limit=limit, offset=offset)
            # embedding은 원본 벡터라 관리자 열람 화면에 노출할 의미가 없고
            # 페이로드만 불필요하게 키운다.
            rows = [dataclasses.replace(e, embedding=None) for e in events]
        case "autobiographies":
            rows = await gateways.autobiographies.list_all(limit=limit, offset=offset)
        case "chapter_drafts":
            rows = await gateways.chapters.list_all(limit=limit, offset=offset)
        case _:
            raise ValueError(f"unknown admin db table: {table}")

    await gateways.audit.record(AdminAuditLogCreateData(admin_id=admin_id, action=f"view_db_table:{table}"))
    await gateways.commit()
    return rows


async def list_audit_logs(
    gateways: Gateways, *, admin_id: uuid.UUID, limit: int, offset: int
) -> list[AdminAuditLogRecord]:
    logs = await gateways.audit.list_recent(limit=limit, offset=offset)
    await gateways.audit.record(AdminAuditLogCreateData(admin_id=admin_id, action="view_audit_logs"))
    await gateways.commit()
    return logs


def get_app_log_lines(*, service: str, lines: int) -> list[str]:
    """backend/worker/beat 로그 파일의 마지막 N줄. 아직 로그가 없으면(파일 없음)
    빈 리스트 — 애러가 아니라 정상 상태로 취급한다(예: beat는 출력이 적어 파일이
    한동안 생기지 않을 수 있다). 감사 로그에는 남기지 않는다 — 운영 로그는
    admin_audit_logs가 추적 대상으로 삼는 "유저의 개인 서사 데이터 열람"이
    아니기 때문(reconcile_stale_sessions와 동일한 근거, 위 문서 참조)."""
    log_path = _LOG_DIR / f"{service}.log"
    if not log_path.exists():
        return []
    with log_path.open(encoding="utf-8", errors="replace") as f:
        # readlines()[-N:]은 파일 전체를 메모리에 올린다 — 로그가 수십 MB로 자라도
        # 마지막 N줄만 유지하는 고정 크기 버퍼로 스트리밍한다(2026-07-18).
        return list(deque(f, maxlen=lines))
