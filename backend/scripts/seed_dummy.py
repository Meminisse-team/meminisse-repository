"""
Meminisse 범용 더미 데이터 시드 스크립트
=========================================

실행 방법 (backend/ 디렉토리에서):

  # 필수 인자만 사용
  python -m scripts.seed_dummy \\
      --email "napoleon@gmail.com" \\
      --password "napoleon!!" \\
      --name "나폴레옹 보나파르트" \\
      --file "C:\\경로\\나폴레옹_더미데이터.txt"

  # 선택 인자까지 포함
  python -m scripts.seed_dummy \\
      --email "napoleon@gmail.com" \\
      --password "napoleon!!" \\
      --name "나폴레옹 보나파르트" \\
      --birth-year 1769 \\
      --hometown "코르시카 아작시오" \\
      --education-level university \\
      --marital-status married \\
      --has-children true \\
      --file "C:\\경로\\나폴레옹_더미데이터.txt"

  # 도움말
  python -m scripts.seed_dummy --help

필수 인자:
  --email       계정 이메일 (예: napoleon@gmail.com)
  --password    계정 비밀번호 (예: napoleon!!)
  --name        인물 한글 이름 (예: 나폴레옹 보나파르트)
  --file        더미 데이터 .txt 파일의 절대 경로

선택 인자:
  --birth-year  출생 연도 (정수, 생략 시 DB에 NULL로 저장)
  --hometown    고향 (생략 시 DB에 NULL로 저장)

더미 데이터 파일 형식 요구사항:
  [질문 N] ... [답변 N] ... 패턴이 반복되어야 합니다.
  (100문 100답 기준이나 그 외 개수도 처리 가능)

멱등성:
  동일 이메일로 재실행하면 기존 public.users + 하위 데이터(세션/채팅 로그 등)를
  모두 삭제 후 재삽입합니다. Unique Constraint 오류가 발생하지 않습니다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

# ── 프로젝트 루트(backend/)를 sys.path에 추가 ──────────────────────────────
_HERE = Path(__file__).resolve().parent   # backend/scripts/
_BACKEND = _HERE.parent                   # backend/
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.enums import (
    EducationLevel,
    MaritalStatus,
    MessageRole,
    SessionStatus,
    SessionType,
    UserRole,
    UserStage,
)
from app.models.interview import ChatLog, InterviewSession
from app.models.question import Question
from app.models.user import User


# ──────────────────────────────────────────────────────────────────────────────
# 1. CLI 인자 파싱
# ──────────────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed_dummy",
        description="Meminisse 더미 데이터 시드 스크립트 — 인물 정보를 인자로 받아 DB에 삽입합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python -m scripts.seed_dummy \\
      --email "napoleon@gmail.com" \\
      --password "napoleon!!" \\
      --name "나폴레옹 보나파르트" \\
      --birth-year 1769 \\
      --hometown "코르시카 아작시오" \\
      --education-level university \\
      --marital-status married \\
      --has-children true \\
      --file "C:\\경로\\나폴레옹_더미데이터.txt"

교육 수준 선택값: elementary | middle_school | high_school | university | graduate_school
혼인 여부 선택값: single | married | divorced | widowed
자녀 여부 선택값: true | false
        """,
    )

    # 필수 인자
    parser.add_argument(
        "--email",
        required=True,
        metavar="EMAIL",
        help="계정 이메일 (예: napoleon@gmail.com)",
    )
    parser.add_argument(
        "--password",
        required=True,
        metavar="PASSWORD",
        help="계정 비밀번호 — Supabase Auth가 bcrypt로 해싱합니다 (예: napoleon!!)",
    )
    parser.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="인물 이름 (예: 나폴레옹 보나파르트)",
    )
    parser.add_argument(
        "--file",
        required=True,
        metavar="FILE_PATH",
        help="더미 데이터 .txt 파일 절대 경로 ([질문 N]/[답변 N] 형식)",
    )

    # 선택 인자
    parser.add_argument(
        "--birth-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="출생 연도 정수 (생략 시 NULL, 예: 1769)",
    )
    parser.add_argument(
        "--hometown",
        default=None,
        metavar="HOMETOWN",
        help="고향 (생략 시 NULL, 예: '코르시카 아작시오')",
    )
    parser.add_argument(
        "--education-level",
        default=None,
        choices=[e.value for e in EducationLevel],
        metavar="LEVEL",
        help=(
            "최종 학력 (생략 시 NULL). "
            "선택값: elementary | middle_school | high_school | university | graduate_school"
        ),
    )
    parser.add_argument(
        "--marital-status",
        default=None,
        choices=[m.value for m in MaritalStatus],
        metavar="STATUS",
        help=(
            "혼인 여부 (생략 시 NULL). "
            "선택값: single | married | divorced | widowed"
        ),
    )
    parser.add_argument(
        "--has-children",
        default=None,
        choices=["true", "false"],
        metavar="BOOL",
        help="자녀 여부 (생략 시 NULL). 선택값: true | false",
    )

    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────────────────
# 2. 더미 데이터 파싱
# ──────────────────────────────────────────────────────────────────────────────
def parse_dummy_data(file_path: Path) -> list[dict]:
    """[질문 N] / [답변 N] 쌍을 파싱하여 [{number, question, answer}, ...] 반환."""
    text_raw = file_path.read_text(encoding="utf-8")

    pattern = re.compile(
        r"\[질문\s*(\d+)\]\s*(.*?)\s*\[답변\s*\1\]\s*(.*?)(?=\[질문\s*\d+\]|\Z)",
        re.DOTALL,
    )
    matches = pattern.findall(text_raw)

    results = [
        {
            "number": int(num_str),
            "question": q_text.strip(),
            "answer": a_text.strip(),
        }
        for num_str, q_text, a_text in matches
    ]
    results.sort(key=lambda x: x["number"])
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 3. Supabase Auth Admin API 헬퍼
# ──────────────────────────────────────────────────────────────────────────────
_TIMEOUT = httpx.Timeout(30.0, connect=15.0)


def _admin_headers() -> dict[str, str]:
    return {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


async def get_or_create_auth_user(
    email: str, password: str, name: str
) -> tuple[uuid.UUID, bool]:
    """
    Supabase auth.users에서 이메일로 사용자를 조회하거나 새로 생성합니다.

    Returns:
        (user_id, is_new)  — is_new=True면 방금 새로 생성, False면 기존 계정
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        list_resp = await client.get(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers=_admin_headers(),
            params={"page": 1, "per_page": 1000},
        )
        list_resp.raise_for_status()
        data = list_resp.json()
        users_list = data.get("users", data) if isinstance(data, dict) else data

        for u in users_list:
            if u.get("email", "").lower() == email.lower():
                print(f"  [Auth] 기존 auth.users 계정 발견 → id={u['id']}")
                return uuid.UUID(u["id"]), False

        create_resp = await client.post(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers=_admin_headers(),
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"name": name},
            },
        )
        create_resp.raise_for_status()
        new_id = uuid.UUID(create_resp.json()["id"])
        print(f"  [Auth] 새 auth.users 계정 생성 → id={new_id}")
        return new_id, True


# ──────────────────────────────────────────────────────────────────────────────
# 4. DB 조작 함수
# ──────────────────────────────────────────────────────────────────────────────
async def wipe_public_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """public.users 행 삭제 — CASCADE로 sessions/chat_logs 등 모두 삭제됨."""
    await db.execute(delete(User).where(User.id == user_id))
    print("  [DB] public.users 기존 데이터 삭제 완료 (CASCADE)")


async def insert_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    email: str,
    name: str,
    birth_year: int | None,
    hometown: str | None,
    education_level: EducationLevel | None,
    marital_status: MaritalStatus | None,
    has_children: bool | None,
) -> User:
    """public.users 프로필 행 삽입."""
    user = User(
        id=user_id,
        email=email,
        name=name,
        birth_year=birth_year,
        hometown=hometown,
        current_stage=UserStage.INTERVIEW,
        role=UserRole.USER,
        education_level=education_level,
        marital_status=marital_status,
        has_children=has_children,
    )
    db.add(user)
    await db.flush()
    print(f"  [DB] public.users 삽입 완료 → id={user_id}")
    return user


async def fetch_questions(db: AsyncSession) -> dict[int, Question]:
    """questions 테이블에서 sequence_order 1~100 조회."""
    result = await db.execute(
        select(Question)
        .where(Question.sequence_order.between(1, 100))
        .order_by(Question.sequence_order)
    )
    questions = result.scalars().all()
    mapping = {q.sequence_order: q for q in questions}
    print(f"  [DB] questions 테이블에서 {len(mapping)}개 조회됨")
    return mapping


async def insert_sessions_and_logs(
    db: AsyncSession,
    user_id: uuid.UUID,
    question_map: dict[int, Question],
    qa_pairs: list[dict],
) -> int:
    """Q&A 쌍마다 InterviewSession(COMPLETED) + ChatLog(USER 답변) 삽입."""
    inserted_count = 0
    skipped_count = 0

    for pair in qa_pairs:
        seq_num = pair["number"]
        answer_text = pair["answer"]

        question = question_map.get(seq_num)
        if question is None:
            print(f"  [경고] sequence_order={seq_num} 질문 없음 → 건너뜀")
            skipped_count += 1
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
        )
        db.add(session)
        await db.flush()

        chat_log = ChatLog(
            id=uuid.uuid4(),
            session_id=session.id,
            role=MessageRole.USER,
            content=answer_text,
            turn_index=0,
        )
        db.add(chat_log)
        inserted_count += 1

    if skipped_count:
        print(f"  [경고] {skipped_count}개 건너뜀 (questions 테이블에 해당 번호 없음)")

    await db.flush()
    return inserted_count


# ──────────────────────────────────────────────────────────────────────────────
# 5. 검증
# ──────────────────────────────────────────────────────────────────────────────
async def verify(db: AsyncSession, user_id: uuid.UUID, expected_count: int) -> None:
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()

    session_result = await db.execute(
        select(InterviewSession).where(InterviewSession.user_id == user_id)
    )
    sessions = session_result.scalars().all()

    answer_count = 0
    if sessions:
        session_ids = [s.id for s in sessions]
        log_result = await db.execute(
            select(ChatLog).where(
                ChatLog.session_id.in_(session_ids),
                ChatLog.role == MessageRole.USER,
            )
        )
        answer_count = len(log_result.scalars().all())

    ok_icon = "✅" if answer_count == expected_count else "❌"

    print()
    print("=" * 60)
    print("  ✅ 검증 결과")
    print("=" * 60)
    if user:
        print(f"  유저 생성 여부  : ✅ 존재 (email={user.email})")
        print(f"  유저 이름       : {user.name}")
        print(f"  유저 ID         : {user.id}")
        print(f"  출생 연도       : {user.birth_year if user.birth_year else 'NULL'}")
        print(f"  고향           : {user.hometown if user.hometown else 'NULL'}")
        print(f"  최종 학력       : {user.education_level.value if user.education_level else 'NULL'}")
        print(f"  혼인 여부       : {user.marital_status.value if user.marital_status else 'NULL'}")
        print(f"  자녀 여부       : {user.has_children if user.has_children is not None else 'NULL'}")
    else:
        print("  유저 생성 여부  : ❌ 없음 (오류!)")
    print(f"  인터뷰 세션 수  : {len(sessions)}개")
    print(
        f"  삽입된 답변 수  : {answer_count}개 "
        f"{ok_icon} (기대값: {expected_count}개)"
    )
    print("=" * 60)


# ──────────────────────────────────────────────────────────────────────────────
# 6. 메인
# ──────────────────────────────────────────────────────────────────────────────
async def main(args: argparse.Namespace) -> None:
    print()
    print("=" * 60)
    print("  Meminisse 범용 더미 데이터 시드 스크립트")
    print(f"  대상 인물: {args.name}")
    print(f"  이메일   : {args.email}")
    print("=" * 60)

    # ── Step 1: 파일 파싱 ───────────────────────────────────────────────────
    print("\n[1단계] 더미 데이터 파싱 중...")
    dummy_path = Path(args.file)
    if not dummy_path.exists():
        print(f"  ❌ 파일을 찾을 수 없습니다: {dummy_path}")
        sys.exit(1)

    qa_pairs = parse_dummy_data(dummy_path)
    print(f"  파싱 완료: {len(qa_pairs)}개 질문-답변 쌍")
    if len(qa_pairs) == 0:
        print("  ❌ 파싱된 Q&A가 0개입니다. 파일 형식을 확인하세요.")
        sys.exit(1)
    if len(qa_pairs) != 100:
        print(f"  ⚠️  경고: 100개가 아닌 {len(qa_pairs)}개 파싱됨 — 계속 진행합니다.")

    # ── Step 2: Supabase Auth ────────────────────────────────────────────────
    print("\n[2단계] Supabase Auth 계정 처리 중...")
    auth_user_id, is_new = await get_or_create_auth_user(
        email=args.email,
        password=args.password,
        name=args.name,
    )

    # ── Step 3: DB 삽입 ─────────────────────────────────────────────────────
    print("\n[3단계] DB 데이터 삽입 중...")
    async with AsyncSessionLocal() as db:
        try:
            await wipe_public_user(db, auth_user_id)
            await insert_user(
                db,
                user_id=auth_user_id,
                email=args.email,
                name=args.name,
                birth_year=args.birth_year,
                hometown=args.hometown,
                education_level=(
                    EducationLevel(args.education_level)
                    if args.education_level else None
                ),
                marital_status=(
                    MaritalStatus(args.marital_status)
                    if args.marital_status else None
                ),
                has_children=(
                    args.has_children.lower() == "true"
                    if args.has_children is not None else None
                ),
            )

            question_map = await fetch_questions(db)
            if not question_map:
                print(
                    "  ❌ questions 테이블이 비어 있습니다.\n"
                    "     alembic upgrade head 를 먼저 실행해 시드 마이그레이션을 적용하세요."
                )
                await db.rollback()
                sys.exit(1)

            inserted = await insert_sessions_and_logs(
                db, auth_user_id, question_map, qa_pairs
            )
            print(f"  [DB] 총 {inserted}개 답변(ChatLog) 삽입 예정")

            await db.commit()
            print("  [DB] 커밋 완료 ✅")

        except Exception as exc:
            await db.rollback()
            print(f"\n  ❌ 오류 발생: {exc}")
            raise

        # ── Step 4: 검증 ────────────────────────────────────────────────────
        print("\n[4단계] 검증 쿼리 실행 중...")
        await verify(db, auth_user_id, expected_count=inserted)

    print()
    print("  🎉 시드 스크립트 완료!")
    print()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
