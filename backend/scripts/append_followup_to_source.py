"""
그림자 계정 없이, 원본 계정에 직접 꼬리질문/답변 턴을 이어붙이는 스크립트.

evals/real_followup_simulation.py(및 이걸 감싸는 scripts/seed_followup_dummy.py)는
"조건 비교(full vs with_followup)"를 공정하게 하려고 원본과 분리된 그림자 계정에
확장 대화를 새로 심는다(evals/real_data_comparison.py 모듈 docstring "그림자
계정이 필요한 이유" 참조 — Phase 3 이벤트 병합이 원본을 mutate해 조건 간 비교를
오염시키기 때문). 이 스크립트는 그 비교 실험용이 아니라 "실사용자가 인터뷰 중
꼬리질문까지 실제로 답한 것처럼 보이는 계정 하나"가 필요할 때 쓴다 — 그림자 계정을
만들지 않고, scripts/seed_dummy.py가 이미 심어둔 원본 계정의 기존 세션에 바로
[assistant(꼬리질문), user(꼬리답변)] 턴을 추가한다.

## 실행 순서

1. scripts/seed_dummy.py로 원본 100문항을 이미 시딩해뒀어야 한다.
2. 이 스크립트로 꼬리질문/답변을 원본 계정의 기존 세션에 이어붙인다.
3. (필요하다면) scripts/process_seeded_sessions.py를 원본 이메일로 그 다음에
   돌려야 이번에 추가된 꼬리질문 턴까지 반영해 이벤트를 추출한다 — 순서를
   바꾸면(먼저 Phase 2를 돌린 뒤 이 스크립트를 실행하면) 이미 추출된 이벤트에는
   꼬리질문 내용이 반영되지 않는다.

## 실행 방법 (backend/ 디렉토리에서)

  # 자동 시뮬레이션 방식 (원본 .txt 파일 필요)
  python -m scripts.append_followup_to_source \\
      --email "napoleon@gmail.com" --file "C:\\경로\\나폴레옹_더미데이터.txt"

  # 사람이 편집한 꼬리질문 답변 방식 (--file보다 우선)
  python -m scripts.append_followup_to_source \\
      --email "napoleon@gmail.com" --followup-audit-file "C:\\경로\\followup_audit_나폴레옹.json"

**주의(윤리적 고지)**: --file 방식은 실존 인물이 실제로 하지 않은 발언을 LLM이
지어내 원본 계정에 직접 써넣는다(evals/real_followup_simulation.py 모듈 docstring
참조) — 벤치마크/테스트용으로만 쓰고, 결과가 실제 서비스 화면에 노출되지 않게
계정을 관리할 것.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # backend/scripts/
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.database import AsyncSessionLocal
from app.gateways.factory import gateways_context
from app.models.enums import MessageRole
from evals import real_followup_simulation
from scripts.seed_dummy import fetch_questions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.append_followup_to_source",
        description="그림자 계정 없이 원본 계정의 기존 세션에 꼬리질문/답변 턴을 직접 이어붙입니다.",
    )
    parser.add_argument("--email", required=True, metavar="EMAIL", help="원본 유저 이메일(scripts/seed_dummy.py로 이미 시딩됨)")
    parser.add_argument(
        "--file",
        default=None,
        metavar="FILE_PATH",
        help="원본 .txt 파일 경로 — 자동 판정+LLM 시뮬레이션으로 꼬리질문 답변을 근사한다.",
    )
    parser.add_argument(
        "--followup-audit-file",
        default=None,
        metavar="FILE_PATH",
        help="evals/followup_trigger_audit.py 출력에 사람이 followup_answer를 채운 파일 — 있으면 --file 대신 사용.",
    )
    return parser.parse_args()


async def main(args: argparse.Namespace) -> None:
    if not args.file and not args.followup_audit_file:
        print("❌ --file 또는 --followup-audit-file 중 하나는 필요합니다.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  꼬리질문 턴을 원본 계정에 직접 이어붙이기 (그림자 계정 없음)")
    print(f"  이메일: {args.email}")
    print("=" * 60)

    async with gateways_context() as gateways:
        source_user = await gateways.users.get_by_email(args.email)
        if source_user is None:
            print(f"  ❌ {args.email}에 해당하는 유저가 없습니다 — scripts/seed_dummy.py를 먼저 실행하세요.")
            sys.exit(1)

        print("\n[1단계] 꼬리질문 답변 준비 중...")
        if args.followup_audit_file:
            augmented_qa = real_followup_simulation.build_augmented_qa_from_audit_file(Path(args.followup_audit_file))
            print(f"  사람이 편집한 답변 파일 사용: {args.followup_audit_file}")
        else:
            augmented_qa = await real_followup_simulation.build_augmented_qa(Path(args.file), name=source_user.name)
            print(f"  자동 판정+LLM 시뮬레이션 완료: {args.file}")

        triggered = [item for item in augmented_qa if item["followup_question"] and item["followup_answer"]]
        print(f"  총 {len(augmented_qa)}개 문항 중 꼬리질문 발동 {len(triggered)}개")

        print("\n[2단계] 원본 세션에 꼬리질문 턴 이어붙이는 중...")
        async with AsyncSessionLocal() as db:
            question_map = await fetch_questions(db)  # sequence_order -> Question
        question_id_to_seq = {q.id: seq for seq, q in question_map.items()}

        sessions = await gateways.sessions.list_by_user(source_user.id)
        session_by_seq = {
            question_id_to_seq[s.question_id]: s for s in sessions if s.question_id in question_id_to_seq
        }

        appended = 0
        skipped = 0
        for item in triggered:
            session = session_by_seq.get(item["number"])
            if session is None:
                print(f"  [경고] 문항 {item['number']}번에 해당하는 원본 세션을 찾을 수 없음 — 건너뜀")
                skipped += 1
                continue
            await gateways.sessions.add_chat_log(session.id, role=MessageRole.ASSISTANT, content=item["followup_question"])
            await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content=item["followup_answer"])
            await gateways.sessions.update_slots(
                session.id, slots_filled=session.slots_filled, followup_count=session.followup_count + 1
            )
            appended += 1

        await gateways.commit()

        print()
        print("=" * 60)
        print("  ✅ 완료")
        print(f"  이어붙인 세션: {appended}개, 건너뜀: {skipped}개")
        print("  다음 단계(필요 시): scripts/process_seeded_sessions.py --email 같은 이메일")
        print("=" * 60)
        print()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
