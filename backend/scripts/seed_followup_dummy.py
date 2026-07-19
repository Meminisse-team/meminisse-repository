"""
꼬리질문까지 답한 "확장 대화"를 그림자 계정에 시딩만 하는 스크립트.

evals/real_followup_simulation.py의 with_followup 조건(run_with_followup_condition)은
시딩 뒤에 Phase 2(이벤트 추출)와 Phase 3/4(원고 생성)까지 항상 이어서 실행하고,
evals/real_data_comparison.py는 거기에 더해 나머지 4개 조건 생성 + 채점까지 전부
돌린다. 시딩된 데이터만 있으면 되고 이벤트 추출·원고 생성·비교 채점은 필요 없을 때
이 스크립트를 쓴다 — real_followup_simulation.build_augmented_qa(_from_audit_file)와
seed_augmented_shadow_user만 호출하고 멈춘다.

시딩 후 Phase 2(이벤트 추출)까지 필요해지면 scripts/process_seeded_sessions.py를
이 스크립트가 만든 그림자 계정 이메일로 이어서 돌리면 된다.

실행 방법 (backend/ 디렉토리에서):

  # 자동 시뮬레이션 방식 (원본 .txt 파일 필요)
  python -m scripts.seed_followup_dummy \\
      --email "napoleon@gmail.com" --password "<그림자 계정용 임시 비밀번호>" \\
      --file "C:\\경로\\나폴레옹_더미데이터.txt"

  # 사람이 편집한 꼬리질문 답변 방식 (--file보다 우선)
  python -m scripts.seed_followup_dummy \\
      --email "napoleon@gmail.com" --password "<그림자 계정용 임시 비밀번호>" \\
      --followup-audit-file "C:\\경로\\followup_audit_나폴레옹.json"

필수 인자:
  --email       scripts/seed_dummy.py로 이미 시딩된 원본 유저 이메일
  --password    새로 생성할 그림자 계정 비밀번호

--file / --followup-audit-file 중 정확히 하나 필요(둘 다 있으면 --followup-audit-file
우선, evals/real_followup_simulation.py와 동일한 규칙).

**주의(윤리적 고지)**: --file 방식은 실존 인물이 실제로 하지 않은 발언을 LLM이
지어내 벤치마크용으로만 시딩한다(evals/real_followup_simulation.py 모듈 docstring
참조) — evals/results/ 밖으로 결과가 나가거나 사용자에게 노출되어서는 안 된다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # backend/scripts/
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.gateways.factory import gateways_context
from evals import real_followup_simulation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed_followup_dummy",
        description="꼬리질문까지 답한 확장 대화를 그림자 계정에 시딩만 합니다(이벤트 추출·원고 생성 없음).",
    )
    parser.add_argument("--email", required=True, metavar="EMAIL", help="원본 유저 이메일(scripts/seed_dummy.py로 이미 시딩됨)")
    parser.add_argument("--password", required=True, metavar="PASSWORD", help="새로 생성할 그림자 계정 비밀번호")
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
    parser.add_argument(
        "--shadow-email",
        default=None,
        metavar="EMAIL",
        help="그림자 계정 이메일(생략 시 원본 이메일에 +with-followup-<난수>를 붙여 자동 생성)",
    )
    return parser.parse_args()


def _default_shadow_email(base_email: str) -> str:
    local, _, domain = base_email.partition("@")
    token = uuid.uuid4().hex[:6]
    return f"{local}+with-followup-{token}@{domain}"


async def main(args: argparse.Namespace) -> None:
    if not args.file and not args.followup_audit_file:
        print("❌ --file 또는 --followup-audit-file 중 하나는 필요합니다.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  꼬리질문 확장 대화 시딩 (이벤트 추출/원고 생성 없음)")
    print(f"  원본 이메일: {args.email}")
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

        triggered = sum(1 for item in augmented_qa if item["followup_question"])
        print(f"  총 {len(augmented_qa)}개 문항 중 꼬리질문 발동 {triggered}개")

        shadow_email = args.shadow_email or _default_shadow_email(source_user.email)
        print(f"\n[2단계] 그림자 계정 시딩 중... (email={shadow_email})")
        shadow_user = await real_followup_simulation.seed_augmented_shadow_user(
            gateways,
            source_user=source_user,
            augmented_qa=augmented_qa,
            shadow_email=shadow_email,
            password=args.password,
        )

        print()
        print("=" * 60)
        print("  ✅ 시딩 완료")
        print(f"  그림자 계정 이메일: {shadow_user.email}")
        print(f"  그림자 유저 ID    : {shadow_user.id}")
        print("  다음 단계(필요 시): scripts/process_seeded_sessions.py --email 위 이메일")
        print("=" * 60)
        print()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
