"""
P3(정량 평가체계) 1단계 — 합성 페르소나 벤치마크 실행기.

각 페르소나 × 사건 하나를 실제 인터뷰 파이프라인(interview_service.add_user_turn)에
그대로 통과시켜 대화 로그를 만들고, 세션 종료 후 실제 Phase 2 후처리
(event_extraction_service.process_completed_session)까지 돌려 추출된 Event를 얻는다.
Celery 워커 없이 동기적으로 직접 호출한다 — 지금 필요한 건 결과를 즉시 파일로 받는
것이지, 실제 운영처럼 비동기 큐잉을 검증하는 게 아니기 때문이다.

안전장치: 이 스크립트는 실행 즉시 GATEWAY_BACKEND를 강제로 "mock"으로 덮어쓴다.
.env가 GATEWAY_BACKEND=postgres로 돼 있어도(실제 이 프로젝트의 현재 설정이 그렇다)
합성 페르소나 데이터가 팀이 쓰는 실제 개발 DB에 절대 섞여 들어가지 않도록 하기 위함
— 그래서 app.* 를 import하기 전에 반드시 os.environ을 먼저 덮어써야 한다(순서 중요).
Solar/임베딩 API 호출 자체는 그대로 실제 Upstage API를 쓴다(파이프라인 실동작을
검증하는 게 목적이므로 이 부분까지 목업하면 의미가 없다).

사용법 (backend/ 에서):
    ..\venv\Scripts\python -m evals.run_benchmark

출력: backend/evals/results/<타임스탬프>/<persona_id>.json 파일 하나씩, 그리고
전체를 모은 summary.json. 다음 단계(DeepEval 라벨추출 정확도 등, 아직 미착수)가
이 JSON의 ground_truth와 extracted_events를 비교해 recall/precision을 낼 수 있도록
설계했다 — README.md 참조.
"""

from __future__ import annotations

import os

os.environ["GATEWAY_BACKEND"] = "mock"  # app.* import 전에 반드시 먼저 실행돼야 한다.

import asyncio  # noqa: E402
import dataclasses  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402
from enum import Enum  # noqa: E402
from pathlib import Path  # noqa: E402

from app.agents import prompts  # noqa: E402
from app.gateways.dto import UserCreateData  # noqa: E402
from app.gateways.factory import gateways_context  # noqa: E402
from app.models.enums import SessionType  # noqa: E402
from app.schemas.interview import SessionCreate  # noqa: E402
from app.services import event_extraction_service, interview_service  # noqa: E402
from evals.persona_agent import generate_persona_turn  # noqa: E402
from evals.personas import PERSONAS, GroundTruthEvent, Persona  # noqa: E402

_RESULTS_DIR = Path(__file__).parent / "results"
# 페르소나 첫 발화 1회 + 꼬리 질문 응답 최대 MAX_FOLLOWUP_PER_EVENT회.
_MAX_TURNS_PER_EVENT = prompts.MAX_FOLLOWUP_PER_EVENT + 1


def _json_default(obj: object) -> object:
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        data = dataclasses.asdict(obj)
        # EventRecord.embedding은 4천~수천 차원 float 벡터라 JSON에 그대로 실으면
        # 파일 하나가 수백 KB로 부풀고 사람이 검토하기도 어렵다 — 이후 DeepEval
        # 단계도 원본 텍스트만 있으면 되고 벡터 자체는 필요 없으므로 제외한다.
        data.pop("embedding", None)
        return data
    raise TypeError(f"{type(obj)} is not JSON serializable")


# 파일럿 도중 Solar 호출 하나가(원인 불명 — client 타임아웃을 90초로 낮췄는데도
# 재현됨, 2026-07-12) 네트워크 계층 아래서 몇 분씩 응답 없이 멈춰 있는 걸 관찰했다.
# 어느 "단계"에서 멈췄는지 알 수 없으면 다음에 또 막혔을 때 똑같이 헤매게 되므로,
# 파이프라인의 각 외부 호출 단계를 개별적으로 타임아웃 씌우고 라벨을 남긴다 —
# 하나가 막혀도 그 단계만 실패 처리하고 해당 페르소나를 건너뛴 채 전체 파일럿은
# 계속 진행된다.
_STAGE_TIMEOUT_SECONDS = 120


async def _stage(label: str, coro):
    try:
        return await asyncio.wait_for(coro, timeout=_STAGE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"[{label}] {_STAGE_TIMEOUT_SECONDS}초 초과") from exc


async def _run_one(persona: Persona, gt: GroundTruthEvent) -> dict:
    async with gateways_context() as gateways:
        user = await gateways.users.create(
            UserCreateData(
                id=uuid.uuid4(),
                email=f"{persona.persona_id}@eval.local",
                name=persona.name,
                birth_year=persona.birth_year,
                hometown=persona.hometown,
            )
        )
        session = await interview_service.create_session(
            gateways, user.id, SessionCreate(session_type=SessionType.FIXED_QUESTION)
        )

        transcript: list[dict[str, str]] = []
        for turn_idx in range(_MAX_TURNS_PER_EVENT):
            persona_utterance = await _stage(
                f"{persona.persona_id} turn{turn_idx} persona_utterance",
                generate_persona_turn(persona=persona, gt=gt, transcript_so_far=transcript),
            )
            transcript.append({"role": "user", "content": persona_utterance})
            print(f"  [턴 {turn_idx}] 페르소나: {persona_utterance[:60]}", file=sys.stderr)

            prev_followup_count = session.followup_count
            _, assistant_log, session = await _stage(
                f"{persona.persona_id} turn{turn_idx} add_user_turn",
                interview_service.add_user_turn(gateways, session, persona_utterance),
            )
            transcript.append({"role": "assistant", "content": assistant_log.content})
            print(f"  [턴 {turn_idx}] 인터뷰어: {assistant_log.content[:60]}", file=sys.stderr)

            # followup_count가 이번 턴에 늘지 않았다면 슬롯이 다 채워졌거나(placeholder
            # 응답 분기) 위기 세이프가드가 발동한 것 — 이 사건에 대해서는 더 물어볼 게
            # 없다는 뜻이므로 종료한다. 정확한 조건은 interview_service.add_user_turn 참조.
            if session.followup_count == prev_followup_count:
                break

        await gateways.sessions.complete(session.id)
        await gateways.commit()

        events = await _stage(
            f"{persona.persona_id} process_completed_session",
            event_extraction_service.process_completed_session(gateways, session.id),
        )
        final_session = await gateways.sessions.get_by_id(session.id)
        assert final_session is not None

        return {
            "persona_id": persona.persona_id,
            "persona_name": persona.name,
            "birth_year": persona.birth_year,
            "hometown": persona.hometown,
            "life_period": gt.life_period,
            "life_period_label": gt.life_period_label,
            "ground_truth": gt,
            "session_id": session.id,
            "transcript": transcript,
            "session_prose": final_session.session_prose,
            "followup_count_used": final_session.followup_count,
            "extracted_events": events,
        }


async def main() -> None:
    run_dir = _RESULTS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    for persona in PERSONAS:
        for gt in persona.ground_truth_events:
            label = f"{persona.persona_id} / {gt.life_period_label}"
            print(f"[진행중] {label}", file=sys.stderr)
            started = time.monotonic()
            try:
                result = await _run_one(persona, gt)
            except Exception:
                print(f"[실패] {label}\n{traceback.format_exc()}", file=sys.stderr)
                continue
            elapsed = time.monotonic() - started
            out_path = run_dir / f"{persona.persona_id}.json"
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
            print(
                f"[완료] {label} — {elapsed:.1f}초, "
                f"이벤트 {len(result['extracted_events'])}건 추출 → {out_path.name}",
                file=sys.stderr,
            )
            summary.append(
                {
                    "persona_id": persona.persona_id,
                    "life_period_label": gt.life_period_label,
                    "turns": len(result["transcript"]) // 2,
                    "extracted_event_count": len(result["extracted_events"]),
                    "elapsed_seconds": round(elapsed, 1),
                }
            )

    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n총 {len(summary)}건 완료. 결과: {run_dir}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
