"""
곽원철 더미 데이터 전용 — "산문 재조립 생략" 시딩 스크립트.

scripts/seed_dummy.py와 달리, 답변 원문을 그대로 session_prose로 저장해 산문
재조립(_reassemble_prose) LLM 호출과 왜곡탐지(NLI)를 건너뛴다. 그 대신 라벨/
이벤트 추출은 실제로 Solar를 호출해 정리한다 —
event_extraction_service.reextract_events_from_edited_prose를 그대로 쓴다
(사용자가 "나의 이야기"를 직접 고쳐 저장했을 때와 동일한 경로: 이미 확정된
텍스트를 입력으로 삼아 재조립·왜곡탐지 없이 라벨만 뽑는다).

seed_dummy.py는 opening(assistant) 턴 없이 답변(user) 하나만 ChatLog로
넣지만, 여기서는 질문 본문을 assistant 턴으로 함께 넣는다 —
event_extraction_service._question_context가 이 첫 assistant 턴을 참고해
one_line_summary 품질을 높이기 때문이다(짧은 답변이 무엇에 대한 것인지
명확해짐).

실행 방법 (backend/ 디렉토리에서):
    python -m scripts.seed_dummy_direct_prose
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.gateways.factory import gateways_context
from app.models.enums import (
    EducationLevel,
    MaritalStatus,
    MessageRole,
    SessionStatus,
    SessionType,
)
from app.models.interview import ChatLog, InterviewSession
from app.models.question import Question
from app.services import event_extraction_service
from scripts.seed_dummy import (
    get_or_create_auth_user,
    insert_user,
    parse_dummy_data,
    wipe_public_user,
)

_EMAIL = "ordinary@dummy.com"
_PASSWORD = "ordinary!!"
_NAME = "곽원철"
_FILE = Path(r"C:\Users\skack\OneDrive\바탕 화면\학술제\Test-Data\곽원철.txt")

# 사용자가 채팅으로 직접 알려준 값 — 파일 헤더 자동추출(parse_profile_header)을
# 쓰지 않는다(이 파일엔 프로필 헤더가 없고, "1남 1녀"는 seed_dummy.py의
# _map_has_children 정규식(숫자+"명"/"있"/"없")으로 못 잡는 표현이라 애초에
# 자동추출 대상이 아니다).
_BIRTH_YEAR = 1960
_HOMETOWN = "부산"
_EDUCATION_LEVEL = EducationLevel.UNIVERSITY
_MARITAL_STATUS = MaritalStatus.MARRIED
_HAS_CHILDREN = True

_RETRYABLE_MAX_ATTEMPTS = 4
_RETRYABLE_BASE_DELAY_SECONDS = 5.0


async def insert_sessions_with_direct_prose(
    user_id: uuid.UUID, qa_pairs: list[dict]
) -> list[uuid.UUID]:
    """세션마다 opening(assistant, 질문 본문) + 답변(user) ChatLog를 넣고,
    session_prose를 답변 원문 그대로 채운다(재조립 생략). session_prose가 이미
    있으므로 이후 process_completed_session의 멱등성 가드에 걸려 재조립을
    다시 타지 않는다 — 이벤트 추출은 별도 패스(reextract_events_from_edited_prose)에서
    수행한다."""
    session_ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Question).where(Question.sequence_order.between(1, 100))
        )
        question_map = {q.sequence_order: q for q in result.scalars().all()}
        if not question_map:
            raise RuntimeError("questions 테이블이 비어 있습니다 — alembic upgrade head 먼저 실행하세요.")

        skipped = 0
        for pair in qa_pairs:
            seq_num = pair["number"]
            question = question_map.get(seq_num)
            if question is None:
                print(f"  [경고] sequence_order={seq_num} 질문 없음 → 건너뜀", file=sys.stderr)
                skipped += 1
                continue

            session = InterviewSession(
                id=uuid.uuid4(),
                user_id=user_id,
                session_type=SessionType.FIXED_QUESTION,
                question_id=question.id,
                status=SessionStatus.COMPLETED,
                slots_filled={},
                followup_count=0,
                is_must_include=False,
                distortion_flagged=False,
                session_prose=pair["answer"],
            )
            db.add(session)
            await db.flush()

            db.add(
                ChatLog(
                    id=uuid.uuid4(),
                    session_id=session.id,
                    role=MessageRole.ASSISTANT,
                    content=question.content,
                    turn_index=0,
                )
            )
            db.add(
                ChatLog(
                    id=uuid.uuid4(),
                    session_id=session.id,
                    role=MessageRole.USER,
                    content=pair["answer"],
                    turn_index=1,
                )
            )
            session_ids.append(session.id)

        await db.commit()
        if skipped:
            print(f"  [경고] 총 {skipped}개 질문 건너뜀", file=sys.stderr)
    return session_ids


async def _extract_labels_for_session(session_id: uuid.UUID) -> int:
    last_exc: Exception | None = None
    for attempt in range(_RETRYABLE_MAX_ATTEMPTS):
        try:
            async with gateways_context() as gateways:
                events = await event_extraction_service.reextract_events_from_edited_prose(
                    gateways, session_id
                )
                return len(events)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            transient = any(
                needle in str(exc)
                for needle in ("429", "too_many_requests", "connection is closed", "InterfaceError")
            )
            if not transient:
                raise
            delay = _RETRYABLE_BASE_DELAY_SECONDS * (2**attempt)
            print(f"    [재시도 {attempt + 1}/{_RETRYABLE_MAX_ATTEMPTS}] {delay:.0f}초 대기...", file=sys.stderr)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def main() -> None:
    print(f"[1단계] {_FILE} 파싱 중...")
    qa_pairs = parse_dummy_data(_FILE)
    print(f"  파싱 완료: {len(qa_pairs)}개 질문-답변 쌍")

    print("\n[2단계] Supabase Auth 계정 처리 중...")
    user_id, is_new = await get_or_create_auth_user(email=_EMAIL, password=_PASSWORD, name=_NAME)
    print(f"  user_id={user_id} (신규={is_new})")

    print("\n[3단계] public.users + 세션(산문 직접 주입) 삽입 중...")
    async with AsyncSessionLocal() as db:
        await wipe_public_user(db, user_id)
        await insert_user(
            db,
            user_id=user_id,
            email=_EMAIL,
            name=_NAME,
            birth_year=_BIRTH_YEAR,
            hometown=_HOMETOWN,
            education_level=_EDUCATION_LEVEL,
            marital_status=_MARITAL_STATUS,
            has_children=_HAS_CHILDREN,
        )
        await db.commit()

    session_ids = await insert_sessions_with_direct_prose(user_id, qa_pairs)
    print(f"  세션 {len(session_ids)}개 삽입 완료 (session_prose = 답변 원문)")

    print(f"\n[4단계] {len(session_ids)}개 세션 라벨 추출 중 (Solar 실제 호출)...")
    total_events = 0
    failed = 0
    for i, session_id in enumerate(session_ids, start=1):
        print(f"  [{i}/{len(session_ids)}] session={session_id} 추출 중...", file=sys.stderr)
        try:
            count = await _extract_labels_for_session(session_id)
            total_events += count
            print(f"    → 이벤트 {count}건", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"    ❌ 실패: {exc!r}", file=sys.stderr)

    print(f"\n완료: {len(session_ids) - failed}/{len(session_ids)}개 세션 처리, 총 이벤트 {total_events}건")
    if failed:
        print("실패한 세션은 이벤트가 비어 있을 수 있습니다 — 스크립트를 다시 실행하면 delete_by_session 후 재추출됩니다.")


if __name__ == "__main__":
    asyncio.run(main())
