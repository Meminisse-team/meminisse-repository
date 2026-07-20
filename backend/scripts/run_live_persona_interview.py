"""
실 서비스 코드 경로로 100문항 인터뷰를 라이브로 진행하는 스크립트.

scripts/seed_dummy.py는 완성된 답변을 DB에 직접 삽입해 Celery/꼬리질문 로직을
전부 건너뛴다. 이 스크립트는 그 반대다 — 실제 회원가입(app.services.user_service.
create_user, 진짜 Supabase Auth 계정 생성)부터 시작해, 세션 하나하나를 실제
프로덕션 함수(interview_service.create_session/add_user_turn)로 만들고 진행한다.
매 턴마다 실제 슬롯 게이팅·꼬리질문 판정이 살아있는 라이브 인터뷰 그대로다 —
꼬리질문이 실제로 뜨면(필수슬롯형/분량부족형/맥락기반형 중 무엇이든) 그 자리에서
"이 인물이라면 이렇게 답했을 법한" 답변을 LLM으로 생성해 in-character로 응답한다
(evals/real_followup_simulation.py의 simulate_followup_answer와 같은 발상이지만,
정적 판정이 아니라 실제 라이브 상태 기계를 그대로 통과한다는 점이 다르다).

.txt 파일의 [질문 N]은 app/data/question_bank.py의 sequence_order와 1:1로
동일한 텍스트다(scripts/seed_dummy.py가 이미 이 전제로 동작 중) — 하지만 라이브
플로우는 프로필 적격성(_question_eligible)에 안 맞는 질문을 자동으로 건너뛸 수
있어(app/services/interview_service.py:_resolve_next_item) 위치(순번) 기준
매칭이 아니라 실제로 배정된 세션의 질문 "본문 텍스트"로 파일의 답변을 찾는다.

실행 방법 (backend/ 디렉토리에서):
    ..\\venv\\Scripts\\python -m scripts.run_live_persona_interview \\
        --email "billgates@dummy.com" --password "billgates!!" \\
        --name "빌 게이츠" \\
        --file "C:\\경로\\빌게이츠(v3).txt"

주의: .env의 GATEWAY_BACKEND=postgres를 그대로 쓴다(실제 DB에 진짜 계정을
만든다). 자서전 집필 단계(consolidate/TOC/챕터 집필)는 이 스크립트가 절대
건드리지 않는다 — 100개 질문 세션을 라이브로 완료하는 데서 멈춘다.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents import prompts
from app.clients import solar
from app.gateways.factory import gateways_context
from app.models.enums import SessionStatus, SessionType
from app.schemas.interview import SessionCreate
from app.schemas.user import UserCreate
from app.services import interview_service, user_service
from scripts.seed_dummy import parse_dummy_data, parse_profile_header

# 세션당 안전 상한(무한루프 방지) — 실제로는 초기 1턴 + 꼬리질문 최대
# MAX_FOLLOWUP_PER_EVENT(2)회 + 마무리 확인 응답 1회 = 최대 4턴이면 충분하지만,
# 예상 못 한 분기를 대비해 여유를 둔다.
_MAX_TURNS_PER_SESSION = 8

# 마무리 확인(WRAP_UP_CHECK_IN_MESSAGE)에 대한 답 — 슬롯이 이미 다 찼고 이
# 발화 자체는 산문 재조립에서 걸러지는 순수 진행 신호이므로(PROSE_REASSEMBLY_
# SYSTEM_PROMPT 예외 3) LLM 호출 없이 자연스러운 문구 하나로 고정한다.
_WRAP_UP_ACKNOWLEDGEMENT = "네, 이 이야기는 이 정도면 충분히 말씀드린 것 같습니다."

_RETRYABLE_MAX_ATTEMPTS = 4
_RETRYABLE_BASE_DELAY_SECONDS = 5.0
_RETRY_JITTER_SECONDS = 3.0

_PERSONA_REPLY_SYSTEM_PROMPT = """\
당신은 실존 인물의 인터뷰 답변 스타일을 모사하는 시뮬레이터입니다. 아래 [인물
프로필], [이번 질문의 원래 답변](이미 이 인물이 서면으로 상세히 답한 내용)을
참고해, 인터뷰어가 추가로 던진 [꼬리질문]에 그 인물이 답했을 법한 짧은 답변
(2~4문장)을 1인칭 구어체로 작성하세요.

- 원 답변에서 이미 확인된 사실·말투·시대적 배경과 모순되지 않게 유지하세요.
- 꼬리질문이 구체적 사실(나이·동행·장소 등)을 물으면, 원 답변에 이미 있는
  내용은 그대로 재사용하고, 없는 내용은 알려진 전기적 사실에 부합하게 자연스럽게
  채우되 확신이 없으면 "정확히는 기억나지 않지만" 같은 완곡 표현을 쓰세요.
- 새로운 사건이나 인물을 창작하지 말고, 원 답변의 내용을 구체화하는 데에만
  집중하세요. 완성된 산문이 아니라 실제 채팅창에 칠 법한 짧고 자연스러운
  구어체 답변만 쓰세요.
- 절대로 "AI", "언어 모델", "롤플레이" 같은 말을 하지 마세요.
"""


async def _generate_persona_reply(
    *, profile_header: str, question: str, original_answer: str, followup_question: str
) -> str:
    messages = [
        {"role": "system", "content": _PERSONA_REPLY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"[인물 프로필]\n{profile_header}\n\n"
                f"[원래 질문]\n{question}\n\n[이번 질문의 원래 답변]\n{original_answer}\n\n"
                f"[꼬리질문]\n{followup_question}"
            ),
        },
    ]
    response = await solar.chat_completion(messages, reasoning_effort="low", max_tokens=300)
    return (response.choices[0].message.content or "").strip()


async def _add_user_turn_with_retry(gateways, session, content: str):
    """429(요청 한도)에 한해 지수 백오프+지터로 재시도한다 — 다른 스크립트
    (process_seeded_sessions.py)와 동일한 이유·패턴."""
    last_exc: Exception | None = None
    for attempt in range(_RETRYABLE_MAX_ATTEMPTS):
        try:
            return await interview_service.add_user_turn(gateways, session, content)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if "429" not in str(exc) and "too_many_requests" not in str(exc):
                raise
            delay = _RETRYABLE_BASE_DELAY_SECONDS * (2**attempt) + random.uniform(0, _RETRY_JITTER_SECONDS)
            print(f"    [429 재시도 {attempt + 1}/{_RETRYABLE_MAX_ATTEMPTS}] {delay:.1f}초 대기...", file=sys.stderr)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def _run_one_question_session(
    *, user_id: uuid.UUID, profile_header: str, qa_by_question_text: dict[str, str], index: int
) -> str:
    """질문 세션 하나를 라이브로 끝까지 진행한다. 반환값은 상태 요약 문자열(로그용)."""
    async with gateways_context() as gateways:
        try:
            session = await interview_service.create_session(
                gateways, user_id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
            )
        except interview_service.NoRemainingQuestionsError:
            return "DONE"

        if session.session_type != SessionType.FIXED_QUESTION or session.question_id is None:
            return f"SKIP(예상치 못한 세션 타입: {session.session_type})"

        question = await gateways.questions.get_by_id(session.question_id)
        question_text = (question.content if question else "").strip()
        original_answer = qa_by_question_text.get(question_text)
        if original_answer is None:
            return f"NO_MATCH(질문 본문과 일치하는 파일 답변을 못 찾음): {question_text[:60]}..."

        user_turn, assistant_turn, session = await _add_user_turn_with_retry(
            gateways, session, original_answer
        )
        turns = 1

        while session.status != SessionStatus.COMPLETED and turns < _MAX_TURNS_PER_SESSION:
            if assistant_turn.content.strip() == prompts.WRAP_UP_CHECK_IN_MESSAGE:
                reply = _WRAP_UP_ACKNOWLEDGEMENT
            else:
                reply = await _generate_persona_reply(
                    profile_header=profile_header,
                    question=question_text,
                    original_answer=original_answer,
                    followup_question=assistant_turn.content,
                )
            user_turn, assistant_turn, session = await _add_user_turn_with_retry(gateways, session, reply)
            turns += 1

        status = "완료" if session.status == SessionStatus.COMPLETED else f"미완료(status={session.status})"
        return f"OK turns={turns} {status} — {question_text[:40]}..."


async def main(*, email: str, password: str, name: str, file_path: Path) -> None:
    profile = parse_profile_header(file_path)
    qa_pairs = parse_dummy_data(file_path)
    qa_by_question_text = {pair["question"].strip(): pair["answer"] for pair in qa_pairs}
    profile_header = file_path.read_text(encoding="utf-8").split("[질문")[0].strip()

    print(f"[프로필] {profile}")
    print(f"[파일] {len(qa_pairs)}개 질문/답변 파싱 완료")

    async with gateways_context() as gateways:
        try:
            user = await user_service.create_user(
                gateways,
                UserCreate(
                    email=email,
                    name=name,
                    password=password,
                    birth_year=profile["birth_year"],
                    hometown=profile["hometown"],
                    education_level=profile["education_level"],
                    marital_status=profile["marital_status"],
                    has_children=profile["has_children"],
                ),
            )
            await gateways.commit()
            print(f"[가입 완료] user_id={user.id}, email={user.email}")
        except user_service.EmailAlreadyRegisteredError:
            existing = await gateways.users.get_by_email(email)
            assert existing is not None
            user = existing
            print(f"[기존 계정 재사용] user_id={user.id}, email={user.email}")

    index = 0
    while True:
        index += 1
        # 세션 하나가 실패해도(예: DB 커넥션이 순간적으로 끊김, 2026-07-19 실사용 중
        # ConnectionDoesNotExistError로 재현 — 이 스크립트 전체가 죽고 나머지 질문을
        # 전혀 못 도는 사고가 있었다) 전체 스크립트가 죽지 않도록 이 레벨에서
        # 재시도한다. get_next_unasked는 OPEN 상태 세션의 질문은 "이미 배정됨"에서
        # 제외하므로(app/gateways/sqlalchemy_gateways.py 참조), 실패로 중간에 멈춘
        # 세션이 있어도 재시도 시 그 질문에 대해 새 세션을 다시 만들 뿐 중복/모순은
        # 생기지 않는다 — 처음 부분만 완료된 낡은 OPEN 세션은 그냥 방치돼도 무해하다
        # (Phase 2 이후 파이프라인은 COMPLETED 세션만 본다).
        result = None
        last_exc: Exception | None = None
        for attempt in range(_RETRYABLE_MAX_ATTEMPTS):
            try:
                result = await _run_one_question_session(
                    user_id=user.id,
                    profile_header=profile_header,
                    qa_by_question_text=qa_by_question_text,
                    index=index,
                )
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                delay = _RETRYABLE_BASE_DELAY_SECONDS * (2**attempt) + random.uniform(0, _RETRY_JITTER_SECONDS)
                print(
                    f"    [세션 오류, 재시도 {attempt + 1}/{_RETRYABLE_MAX_ATTEMPTS}] {exc!r} "
                    f"— {delay:.1f}초 대기...",
                    file=sys.stderr,
                )
                await asyncio.sleep(delay)

        if last_exc is not None:
            print(f"[{index}] FAILED(재시도 소진): {last_exc!r} — 이 질문은 건너뛰고 계속 진행합니다.")
            continue

        if result == "DONE":
            print(f"[{index}] 더 배정할 질문 없음 — 전체 종료")
            break
        print(f"[{index}] {result}")

    print("\n완료: 질문 세션 100개(또는 배정 가능한 만큼) 진행을 마쳤습니다.")
    print("자서전 집필 단계(이야기 정리/목차/챕터 집필)는 이 스크립트가 트리거하지 않습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실 서비스 코드 경로로 100문항 인터뷰를 라이브로 진행합니다.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--file", required=True, dest="file_path", type=Path)
    args = parser.parse_args()
    asyncio.run(main(email=args.email, password=args.password, name=args.name, file_path=args.file_path))
