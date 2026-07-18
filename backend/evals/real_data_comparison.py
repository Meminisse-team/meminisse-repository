"""
기획안 6절 "비교 실험 설계(베이스라인 및 어블레이션)"를 실제 유명인 더미 데이터에
적용하는 스크립트. evals/baseline_ablation_comparison.py(합성 페르소나,
evals/personas.py 기반, Mock DB)와는 입력 출처와 DB 백엔드가 다르다 — 이쪽은
scripts/seed_dummy.py로 실제 Postgres DB에 시딩하고 scripts/process_seeded_
sessions.py로 Phase 2까지 처리를 마친 "진짜" 유저 데이터를 대상으로 한다.

## 조건 5가지와 "그림자 계정"이 필요한 이유

- full: 시딩된 원본 유저 그대로 Phase 3/4.
- baseline: 원본 유저의 답변 전체를 그대로 "부풀리는" 단일 LLM 호출(이벤트
  추출·RAG·검증 전부 생략).
- no_dynamic_toc: 원본과 동일한 이벤트를 별도 계정에 복제한 뒤, 고정 연대순
  목차로 Phase 3/4(evals/baseline_and_ablations.run_no_dynamic_toc_for_user 재사용).
- no_event_split: 원본 이벤트를 하나로 합쳐 별도 계정에 복제한 뒤, 동적 목차
  그대로 Phase 3/4.
- with_followup: 꼬리질문 유무 비교(Test A)를 실제 데이터에 적용한 조건
  (evals/real_followup_simulation.py, 2026-07-18 추가). 100문항 더미 데이터는
  이미 완결된 답변이라 "꼬리질문을 뺀 버전"이 아니라 반대로 "꼬리질문까지 답한
  버전"을 새로 만들어야 한다 — 즉 기존 full 조건이 사실상 이 축에서는
  "no_followup" 역할을 한다. 시뮬레이션 방식(실존 인물의 가상 답변 생성)에 대한
  윤리적 고지는 real_followup_simulation.py 모듈 docstring 참조. --file 인자가
  있어야만 활성화된다(원본 질문/프로필 헤더 텍스트가 필요해서 DB만으로는
  재구성 불가).

Phase 3(consolidate_autobiography)의 이벤트 병합 판정(_merge_duplicate_events)은
대상 유저의 Event 행을 직접 mutate한다 — 같은 유저에게 여러 조건을 순차로
돌리면 앞선 조건의 병합 결과가 다음 조건의 "병합 전" 상태를 오염시킨다.
evals/baseline_and_ablations.py가 Mock 페르소나마다 매번 새 Mock DB를 만들어
격리하는 것과 동일한 이유로, 여기서는 조건마다 "그림자 계정"(원본 이벤트를
복제해 담은 새 유저)을 만들어 격리한다.

## 실행 순서 (backend/ 디렉토리에서)

    ..\\venv\\Scripts\\python -m scripts.seed_dummy --email "billgates@example.com" \\
        --password "<임시 비밀번호>" --name "빌 게이츠" --file "C:\\...\\빌게이츠(v2).txt"
    ..\\venv\\Scripts\\python -m scripts.process_seeded_sessions --email "billgates@example.com"
    ..\\venv\\Scripts\\python -m evals.real_data_comparison \\
        --email "billgates@example.com" --password "<그림자 계정용 임시 비밀번호>" \\
        --file "C:\\...\\빌게이츠(v2).txt"

--file을 생략하면 with_followup 조건만 건너뛰고 나머지 4개는 그대로 실행된다.

**경고**: 이 스크립트는 실제 Supabase Auth 계정을 최대 3개(no_dynamic_toc/
no_event_split/with_followup용)까지 새로 만들고, 실제 Upstage API를 호출한다
(Phase 3/4 전체 × 4계정 + baseline 1회 + with_followup의 Phase 2 재처리(최대
100세션) + 채점). with_followup까지 포함하면 인물 1명당 시간·비용이 나머지
4개 조건보다 훨씬 크다(_WITH_FOLLOWUP_TIMEOUT_SECONDS=1800 참조). 30명을 이
방식으로 다 돌리면 계정이 최대 90개(3개×30명)까지 추가로 생긴다 — 반복 실행
시 매번 새 계정이 생기므로(멱등성 없음, 아래 _shadow_email 참조), 테스트 삼아
여러 번 돌렸다면 Supabase 대시보드에서 정리할 것.

여러 유명인의 결과를 모아 Wilcoxon 검정까지 하려면 --email/--file을 바꿔가며
이 스크립트를 유명인 수만큼 반복 실행한 뒤(각자 evals/results/.../real_<email
local part>.json 생성), evals/baseline_ablation_comparison.py의 _aggregate를
재사용하는 evals/real_data_aggregate.py로 합산한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # backend/evals/
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.clients import embeddings as embeddings_client
from app.clients import solar
from app.gateways.dto import EventCreateData, UserCreateData, UserRecord
from app.gateways.factory import Gateways, gateways_context
from app.models.enums import EventSourceType
from app.services import autobiography_service
from evals import baseline_and_ablations, information_preservation, real_followup_simulation
from evals.baseline_ablation_comparison import _score_coherence
from evals.deepeval_narrative_coherence import _run_phase34
from evals.solar_judge_model import SolarJudgeModel
from scripts.seed_dummy import get_or_create_auth_user

_RESULTS_DIR = Path(__file__).parent / "results"
_CONDITION_TIMEOUT_SECONDS = 240
# with_followup은 Phase 2(이벤트 추출)를 최대 100세션분 새로 돈 뒤에야 Phase 3/4를
# 시작한다(evals/real_followup_simulation.py) — 나머지 조건(이미 추출된 이벤트만
# 재구성)보다 훨씬 오래 걸려 별도의 넉넉한 타임아웃이 필요하다.
_WITH_FOLLOWUP_TIMEOUT_SECONDS = 1800
_CONDITIONS = ["full", "baseline", "no_dynamic_toc", "no_event_split", "with_followup"]


def _shadow_email(base_email: str, *, suffix: str) -> str:
    """조건별 그림자 계정 이메일 — 실행마다 겹치지 않도록 짧은 난수를 붙인다
    (모듈 docstring "경고" 참조: 멱등성이 없어 반복 실행 시 계정이 누적된다)."""
    local, _, domain = base_email.partition("@")
    token = uuid.uuid4().hex[:6]
    return f"{local}+{suffix}-{token}@{domain}"


async def _get_raw_input_text(gateways: Gateways, user_id: uuid.UUID) -> str:
    """유저의 100문항 답변 원문(ChatLog, role=user)을 전부 이어붙인다 —
    evals/information_preservation.raw_input_text_from_persona_result와 동일한
    개념(재조립 산문이 아니라 화자가 실제로 쓴 답변 원문)을 실제 DB에서 재구성."""
    sessions = await gateways.sessions.list_by_user(user_id)
    chunks: list[str] = []
    for session_summary in sessions:
        session = await gateways.sessions.get_by_id(session_summary.id)
        chunks.extend(log.content for log in session.chat_logs if log.role.value == "user")
    return "\n".join(chunks)


async def _clone_events_to_shadow_user(
    gateways: Gateways,
    *,
    source_user: UserRecord,
    event_dicts: list[dict],
    email_suffix: str,
    password: str,
) -> UserRecord:
    """source_user의 이벤트(들)를 새 "그림자" 계정에 그대로(또는 병합된 형태로)
    복제한다 — 모듈 docstring "그림자 계정이 필요한 이유" 참조. event_dicts는
    evals.baseline_and_ablations._event_record_to_merge_dict와 같은 모양의 dict
    리스트(개별 이벤트 그대로 복제하려면 원본 이벤트마다 하나씩, 병합해서 복제하려면
    merge_event_records 결과 하나만 담아 호출한다)."""
    email = _shadow_email(source_user.email, suffix=email_suffix)
    auth_user_id, _ = await get_or_create_auth_user(email=email, password=password, name=f"{source_user.name} ({email_suffix})")
    shadow_user = await gateways.users.create(
        UserCreateData(
            id=auth_user_id,
            email=email,
            name=f"{source_user.name} ({email_suffix})",
            birth_year=source_user.birth_year,
            hometown=source_user.hometown,
        )
    )

    create_data = [
        EventCreateData(
            user_id=shadow_user.id,
            source_type=EventSourceType(item["source_type"]),
            one_line_summary=item["one_line_summary"],
            prose_paragraph=item["prose_paragraph"],
            verified=True,
            occurred_at_label=item.get("occurred_at_label"),
            place=item.get("place"),
            people=item.get("people"),
            emotion_tag=item.get("emotion_tag"),
            emotion_intensity=item.get("emotion_intensity"),
            emotion_inferred=bool(item.get("emotion_inferred", False)),
            labels=item.get("labels") or {},
        )
        for item in event_dicts
    ]
    events = await gateways.events.bulk_create(create_data)
    vectors = await embeddings_client.embed_passages([e.prose_paragraph for e in events])
    await gateways.events.bulk_update_embeddings([(e.id, v) for e, v in zip(events, vectors)])
    await gateways.commit()
    return shadow_user


async def run_full(gateways: Gateways, user: UserRecord) -> dict:
    return await _run_phase34(gateways, user)


async def run_baseline(raw_input_text: str, *, persona_name: str) -> dict:
    response = await solar.chat_completion(
        [
            {"role": "system", "content": baseline_and_ablations._BASELINE_SYSTEM_PROMPT},
            {"role": "user", "content": f"[챕터 제목] 나의 이야기\n\n[인터뷰 정리본]\n{raw_input_text}"},
        ],
        reasoning_effort="low",
    )
    return {
        "title": f"{persona_name}의 이야기",
        "book_synopsis": None,
        "final_content": response.choices[0].message.content or "",
    }


async def run_no_dynamic_toc(gateways: Gateways, source_user: UserRecord, *, password: str) -> dict:
    events = await gateways.events.list_unmerged_verified(source_user.id)
    event_dicts = [baseline_and_ablations._event_record_to_merge_dict(e) for e in events]
    shadow_user = await _clone_events_to_shadow_user(
        gateways, source_user=source_user, event_dicts=event_dicts, email_suffix="no-dynamic-toc", password=password
    )
    return await baseline_and_ablations.run_no_dynamic_toc_for_user(gateways, shadow_user.id)


async def run_no_event_split(gateways: Gateways, source_user: UserRecord, *, password: str) -> dict:
    events = await gateways.events.list_unmerged_verified(source_user.id)
    merged = baseline_and_ablations.merge_event_records(events)
    shadow_user = await _clone_events_to_shadow_user(
        gateways, source_user=source_user, event_dicts=[merged], email_suffix="no-event-split", password=password
    )
    return await _run_phase34(gateways, shadow_user)


async def evaluate_one_persona(email: str, *, password: str, file_path: Path | None = None) -> dict:
    async with gateways_context() as gateways:
        source_user = await gateways.users.get_by_email(email)
        if source_user is None:
            raise ValueError(f"{email}에 해당하는 유저가 없습니다 — seed_dummy.py를 먼저 실행하세요.")

        raw_input_text = await _get_raw_input_text(gateways, source_user.id)
        judge = SolarJudgeModel()

        runners = {
            "full": run_full(gateways, source_user),
            "baseline": run_baseline(raw_input_text, persona_name=source_user.name),
            "no_dynamic_toc": run_no_dynamic_toc(gateways, source_user, password=password),
            "no_event_split": run_no_event_split(gateways, source_user, password=password),
        }
        active_conditions = list(_CONDITIONS)
        if file_path is not None:
            runners["with_followup"] = real_followup_simulation.run_with_followup_condition(
                gateways,
                source_user,
                file_path=file_path,
                shadow_email=_shadow_email(source_user.email, suffix="with-followup"),
                password=password,
            )
        else:
            # --file 없이는 with_followup의 원본 질문/답변 텍스트(시뮬레이터 프롬프트에
            # 필요)를 재구성할 수 없다 — DB의 ChatLog만으로는 헤더(프로필) 블록을
            # 복원 못 하므로, 이 조건만 건너뛴다(나머지 4개 조건은 그대로 진행).
            print("  [건너뜀] with_followup: --file 인자가 없어 원본 텍스트를 복원할 수 없음", file=sys.stderr)
            active_conditions = [c for c in active_conditions if c != "with_followup"]

        per_condition: dict = {}
        for name in active_conditions:
            print(f"  [조건] {name} 생성 중...", file=sys.stderr)
            timeout = _WITH_FOLLOWUP_TIMEOUT_SECONDS if name == "with_followup" else _CONDITION_TIMEOUT_SECONDS
            try:
                manuscript = await asyncio.wait_for(runners[name], timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                print(f"  [실패] {name}: {exc!r}", file=sys.stderr)
                per_condition[name] = {"error": repr(exc)}
                continue

            final_content = manuscript.get("final_content") or ""
            try:
                coherence = await asyncio.wait_for(_score_coherence(judge, manuscript), timeout=_CONDITION_TIMEOUT_SECONDS)
                info = await asyncio.wait_for(
                    information_preservation.evaluate_manuscript(
                        raw_input_text=raw_input_text, final_content=final_content
                    ),
                    timeout=_CONDITION_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  [채점 실패] {name}: {exc!r}", file=sys.stderr)
                per_condition[name] = {
                    "title": manuscript.get("title"),
                    "final_content_length": len(final_content),
                    "error": f"scoring failed: {exc!r}",
                }
                continue

            per_condition[name] = {
                "title": manuscript.get("title"),
                "final_content_length": len(final_content),
                "narrative_coherence": coherence,
                "information_preservation": info,
            }

        return per_condition


async def main(email: str, *, password: str, file_path: Path | None) -> None:
    print(f"[평가 중] {email}", file=sys.stderr)
    per_condition = await evaluate_one_persona(email, password=password, file_path=file_path)

    run_dir = _RESULTS_DIR / f"real_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    local_part = email.split("@")[0]
    out_path = run_dir / f"real_{local_part}.json"
    out_path.write_text(json.dumps(per_condition, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== {email} 조건별 결과 ===")
    for name, result in per_condition.items():
        if "error" in result and "narrative_coherence" not in result:
            print(f"  {name:16s} 실패: {result['error']}")
            continue
        coherence = result.get("narrative_coherence")
        precision = (result.get("information_preservation") or {}).get("precision")
        print(
            f"  {name:16s} coherence={coherence if coherence is None else f'{coherence:.2f}'}  "
            f"precision={precision if precision is None else f'{precision:.2f}'}  "
            f"길이={result.get('final_content_length')}"
        )
    print(f"\n상세 결과: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실제 시딩된 유명인 데이터로 베이스라인/어블레이션 비교를 1명분 실행합니다.")
    parser.add_argument("--email", required=True, help="scripts/seed_dummy.py로 시딩하고 process_seeded_sessions.py까지 처리한 유저 이메일")
    parser.add_argument("--password", required=True, help="그림자 계정(no_dynamic_toc/no_event_split/with_followup) 생성용 임시 비밀번호")
    parser.add_argument(
        "--file",
        required=False,
        default=None,
        help="seed_dummy.py에 넘겼던 것과 동일한 .txt 파일 경로 — with_followup 조건에 필요(생략하면 이 조건만 건너뜀)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.email, password=args.password, file_path=Path(args.file) if args.file else None))
