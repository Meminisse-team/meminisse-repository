"""
Test A(꼬리질문 유무가 최종 원고 품질에 미치는 영향)를 실제 유명인 100문항
데이터에 적용하기 위한 다섯 번째 조건 "with_followup" 생성기.

## 왜 필요한가

evals/real_data_comparison.py의 기존 4개 조건(full/baseline/no_dynamic_toc/
no_event_split)은 전부 "이미 존재하는 100개 답변"만 재구성한다. 그런데 그 100개
답변 자체가 이미 "꼬리질문 없이 완결된" 상태다 — 즉 기존 full 조건이 사실상
합성 페르소나 쪽의 no_followup(꼬리질문 제거)에 해당한다. Test A를 real-data
경로에 적용하려면 반대 방향, 즉 "꼬리질문까지 답했다면 어떻게 됐을까"를 만들어야
하는데, 실제 인물(다수는 고인)에게 라이브로 되물을 수 없으므로 다음과 같이
근사한다:

1. evals/followup_trigger_audit.py(Test B)가 이미 만드는 꼬리질문 판정 +
   질문 텍스트를 그대로 재사용한다(같은 판정 로직을 두 번 짜지 않기 위해).
2. 꼬리질문이 발동하는 답변마다, 그 인물의 프로필 헤더(출생연도·학력·인간관계 등,
   .txt 파일 상단)와 해당 질문·답변을 근거로 LLM이 "이 인물이 답했을 법한"
   답변을 하나 더 생성한다(evals/persona_agent.py가 가상 페르소나에게 하는 것과
   같은 방식의 시뮬레이션이지만, 대상이 실존 인물이라는 점이 다르다).
3. 원본 답변 + 시뮬레이션된 꼬리질문 답변을 이어붙인 "확장 대화"를 새 그림자
   계정에 심고, 실제 Phase 2(이벤트 추출)부터 다시 돌린다.

**주의(윤리적 고지)**: 이 조건은 실존 인물(다수가 역사적 위인)이 실제로 하지
않은 발언을 LLM이 지어내 시스템 성능 비교에만 쓴다 — 최종 산출물이 아니라
내부 벤치마크 중간 데이터이며, evals/results/ 밖으로 나가거나 사용자에게
노출되어서는 안 된다. 2026-07-18 사용자 확인 후 진행.

## 사람이 직접 준비한 꼬리질문 답변이 있는 경우 (2026-07-18 추가)

위 2번(LLM 시뮬레이션)은 자동화된 근사치일 뿐이다 — 사람이 Test B 결과
(evals/results/followup_audit_<파일명>.json)를 직접 열어 각 항목에
`followup_answer` 필드를 채워 넣는 방식으로 더 신경 쓴 답변을 준비했다면,
그걸 재판정·재시뮬레이션 없이 그대로 써야 한다. 이유 둘:
(1) LLM 판정은 비결정적이라 재실행하면 원래 Test B가 냈던 followup_question
목록과 다시 어긋날 수 있고, (2) 사람이 이미 들인 수고를 버리고 다시 자동
생성으로 덮어쓰는 건 품질 역행이다. build_augmented_qa_from_audit_file이
이 경로를 담당하며, run_with_followup_condition에 file_path 대신
audit_file_path를 넘기면 이쪽을 탄다.

기대 스키마(Test B 원본 출력에 followup_answer만 추가):
    {"number": 1, "question": "...", "answer": "...",
     "category": "필수슬롯형_꼬리질문", "followup_question": "...",
     "followup_answer": "<사람이 채워 넣은 답변>"}
followup_question이 있는데 followup_answer가 비어 있는 항목이 하나라도 있으면
즉시 에러를 내고 중단한다(조용히 건너뛰면 "꼬리질문 다 답한 줄 알았는데
일부만 반영됐다"는 걸 나중에야 알게 되는 사고로 이어지므로).

## 실행 방법

evals/real_data_comparison.py --file 또는 --followup-audit-file 인자로 자동
포함된다(아래 참조) — 이 모듈을 단독 실행하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.clients import solar
from app.database import AsyncSessionLocal
from app.gateways.dto import SessionCreateData, UserCreateData, UserRecord
from app.gateways.factory import Gateways
from app.models.enums import MessageRole, SessionType
from app.services import event_extraction_service
from evals.deepeval_narrative_coherence import _run_phase34
from evals.followup_trigger_audit import _classify_with_retry
from scripts.seed_dummy import fetch_questions, get_or_create_auth_user, parse_dummy_data

_STAGE_TIMEOUT_SECONDS = 60

_SIMULATE_SYSTEM_PROMPT = """\
당신은 실존 인물의 인터뷰 답변 스타일을 모사하는 시뮬레이터입니다. 아래 [인물
프로필], [원 질문], [원 답변]을 참고해, 인터뷰어가 추가로 던진 [꼬리질문]에
그 인물이 답했을 법한 짧은 답변(2~4문장)을 1인칭으로 작성하세요.

- 원 답변에서 이미 확인된 사실·말투·시대적 배경과 모순되지 않게 유지하세요.
- 꼬리질문이 구체적 사실(나이·동행 등)을 물으면, 알려진 전기적 사실에 부합하는
  값을 채우되 불확실하면 "정확히 기억나진 않지만" 같은 자연스러운 완곡 표현을
  쓰세요 — 확신 없는 사실을 단정적으로 지어내지 마세요.
- 새로운 사건이나 인물을 창작하지 말고, 원 답변의 내용을 구체화하는 데에만
  집중하세요.
"""


async def simulate_followup_answer(
    *, name: str, profile_header: str, question: str, original_answer: str, followup_question: str
) -> str:
    user_content = (
        f"[인물 프로필]\n{profile_header}\n\n"
        f"[원 질문]\n{question}\n\n[원 답변]\n{original_answer}\n\n[꼬리질문]\n{followup_question}"
    )
    response = await solar.chat_completion(
        [
            {"role": "system", "content": _SIMULATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        reasoning_effort="low",
        max_tokens=300,
    )
    return response.choices[0].message.content or ""


def extract_profile_header(file_path: Path) -> str:
    """.txt 파일의 [질문 1] 이전 헤더 블록(출생연도·고향·학력·인간관계 등) 원문을
    그대로 반환한다 — scripts.seed_dummy.parse_profile_header는 구조화된 필드만
    뽑지만, 여기서는 시뮬레이터 프롬프트에 그대로 넣을 자유 텍스트가 필요하다."""
    text = file_path.read_text(encoding="utf-8")
    return text.split("[질문")[0].strip() if "[질문" in text else text[:800].strip()


async def build_augmented_qa(file_path: Path, *, name: str) -> list[dict[str, Any]]:
    """각 답변에 Test B(followup_trigger_audit) 판정을 돌리고, 발동하는 답변마다
    시뮬레이션된 후속 답변을 붙인다. 발동하지 않는 답변은 followup_question/
    followup_answer가 둘 다 None으로 남는다."""
    profile_header = extract_profile_header(file_path)
    qa_pairs = parse_dummy_data(file_path)

    augmented: list[dict[str, Any]] = []
    for pair in qa_pairs:
        classification = await _classify_with_retry(pair["question"], pair["answer"])
        followup_question = classification.get("followup_question")
        followup_answer = None
        if followup_question:
            followup_answer = await simulate_followup_answer(
                name=name,
                profile_header=profile_header,
                question=pair["question"],
                original_answer=pair["answer"],
                followup_question=followup_question,
            )
        augmented.append(
            {
                "number": pair["number"],
                "question": pair["question"],
                "answer": pair["answer"],
                "followup_category": classification["category"],
                "followup_question": followup_question,
                "followup_answer": followup_answer,
            }
        )
    return augmented


def build_augmented_qa_from_audit_file(audit_file_path: Path) -> list[dict[str, Any]]:
    """모듈 docstring "사람이 직접 준비한 꼬리질문 답변이 있는 경우" 참조.
    build_augmented_qa의 대안 — Test B 출력을 사람이 직접 편집해 followup_answer를
    채운 파일을 그대로 augmented_qa 형태로 변환한다. LLM 재판정·재시뮬레이션을
    전혀 하지 않는다(비결정성으로 원본 판정과 어긋나는 것을 방지 + 이미 들인
    수고 보존)."""
    data = json.loads(audit_file_path.read_text(encoding="utf-8"))
    augmented: list[dict[str, Any]] = []
    missing_answer_numbers: list[int] = []

    for r in data["results"]:
        followup_question = r.get("followup_question")
        followup_answer = r.get("followup_answer")
        if followup_question and not followup_answer:
            missing_answer_numbers.append(r["number"])
        augmented.append(
            {
                "number": r["number"],
                "question": r["question"],
                "answer": r["answer"],
                "followup_category": r.get("category"),
                "followup_question": followup_question,
                "followup_answer": followup_answer,
            }
        )

    if missing_answer_numbers:
        raise ValueError(
            f"{audit_file_path}: followup_question은 있는데 followup_answer가 비어 있는 "
            f"문항 번호 {missing_answer_numbers} — 전부 채운 뒤 다시 실행하세요."
        )
    return augmented


async def seed_augmented_shadow_user(
    gateways: Gateways, *, source_user: UserRecord, augmented_qa: list[dict[str, Any]], shadow_email: str, password: str
) -> UserRecord:
    """확장 대화(원 답변 + 시뮬레이션된 꼬리질문 답변)를 새 그림자 계정에 심는다 —
    scripts/seed_dummy.py의 insert_sessions_and_logs와 같은 구조지만, 꼬리질문이
    발동한 문항은 세션 하나에 [user, assistant(꼬리질문), user(꼬리답변)] 3턴을
    담는다는 점이 다르다(원본은 1턴)."""
    auth_user_id, _ = await get_or_create_auth_user(
        email=shadow_email, password=password, name=f"{source_user.name} (with-followup)"
    )
    shadow_user = await gateways.users.create(
        UserCreateData(
            id=auth_user_id,
            email=shadow_email,
            name=f"{source_user.name} (with-followup)",
            birth_year=source_user.birth_year,
            hometown=source_user.hometown,
        )
    )

    async with AsyncSessionLocal() as db:
        question_map = await fetch_questions(db)

    skipped = 0
    for pair in augmented_qa:
        question = question_map.get(pair["number"])
        if question is None:
            skipped += 1
            continue
        session = await gateways.sessions.create(
            SessionCreateData(user_id=shadow_user.id, session_type=SessionType.FIXED_QUESTION, question_id=question.id)
        )
        await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content=pair["answer"])
        if pair["followup_question"] and pair["followup_answer"]:
            await gateways.sessions.add_chat_log(session.id, role=MessageRole.ASSISTANT, content=pair["followup_question"])
            await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content=pair["followup_answer"])
        await gateways.sessions.complete(session.id)

    await gateways.commit()
    if skipped:
        print(f"    [경고] question_bank에 없는 sequence_order {skipped}건 건너뜀", file=sys.stderr)
    return shadow_user


async def run_with_followup_condition(
    gateways: Gateways,
    source_user: UserRecord,
    *,
    file_path: Path | None = None,
    audit_file_path: Path | None = None,
    shadow_email: str,
    password: str,
) -> dict[str, Any]:
    """with_followup 조건의 진입점 — evals/real_data_comparison.py가 호출한다.

    file_path/audit_file_path 중 정확히 하나만 준다. audit_file_path가 있으면
    사람이 직접 편집한 꼬리질문 답변(모듈 docstring 참조)을 그대로 쓰고,
    file_path만 있으면 자동 판정+LLM 시뮬레이션(build_augmented_qa)으로
    근사한다."""
    if audit_file_path is not None and file_path is not None:
        raise ValueError("file_path와 audit_file_path 중 하나만 지정하세요.")
    if audit_file_path is not None:
        augmented_qa = build_augmented_qa_from_audit_file(audit_file_path)
    elif file_path is not None:
        augmented_qa = await build_augmented_qa(file_path, name=source_user.name)
    else:
        raise ValueError("file_path 또는 audit_file_path 중 하나는 필요합니다.")

    shadow_user = await seed_augmented_shadow_user(
        gateways, source_user=source_user, augmented_qa=augmented_qa, shadow_email=shadow_email, password=password
    )

    sessions = await gateways.sessions.list_by_user(shadow_user.id)
    for i, session_summary in enumerate(sessions, start=1):
        print(f"    [with_followup Phase2] {i}/{len(sessions)}", file=sys.stderr)
        try:
            await asyncio.wait_for(
                event_extraction_service.process_completed_session(gateways, session_summary.id),
                timeout=_STAGE_TIMEOUT_SECONDS * 2,
            )
            await gateways.commit()
        except Exception as exc:  # noqa: BLE001 — 세션 하나 실패해도 나머지는 계속
            print(f"    [실패] session={session_summary.id}: {exc!r}", file=sys.stderr)

    return await _run_phase34(gateways, shadow_user)
