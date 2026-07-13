"""
evals/README.md 3절 "G-Eval 서사일관성".

인터뷰(Phase 1~2)가 아니라 자서전 챕터 집필(Phase 4) 결과물을 평가하는 지표라,
evals/results/<타임스탬프>/<persona_id>.json에 이미 저장된 extracted_events를 Mock
게이트웨이에 재구성한 뒤 Phase 3(병합·중요도·스타일 바이블) → Phase 4(목차→시놉시스→
챕터 집필→통일성 윤문)까지 실제로 돌려(evals/README.md 2절과 마찬가지로 실제 Solar
호출) 완성된 챕터를 얻는다. 각 챕터에 DeepEval의 GEval 메트릭(판정 모델:
evals/solar_judge_model.SolarJudgeModel, "Solar를 judge로 통일" 결정 — README 2절)으로
서사일관성 점수를 매긴다.

인터뷰 파이프라인(Phase 1~2)을 다시 돌리지 않고 이미 저장된 extracted_events를
그대로 재구성해 쓰는 이유: (1) 같은 Solar 호출을 반복해 비용/시간을 낭비하지 않기
위해, (2) 간헐적 후처리 지연 문제(evals/README.md 1절, 원인 미상)가 있는 이 환경에서
인터뷰+Phase2를 다시 거치면 불필요하게 그 리스크에 다시 노출되기 때문이다 — Phase
3/4만 새로 도는 이 스크립트는 그 문제와 무관하다(NLI 로컬 추론도 거의 없음 —
groundedness 체크 정도).

실행:
    cd backend
    ../venv/Scripts/python -m evals.deepeval_narrative_coherence
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams

from app.clients import embeddings
from app.gateways.dto import EventCreateData, SessionCreateData, UserCreateData, UserRecord
from app.gateways.factory import Gateways, _build_mock_gateways
from app.models.enums import EventSourceType, LifePeriod, SessionType
from app.services import autobiography_service
from evals.solar_judge_model import SolarJudgeModel

_RESULTS_DIR = Path(__file__).parent / "results"

_NARRATIVE_COHERENCE_CRITERIA = (
    "주어진 자서전 챕터 산문이 시간 순서와 인과관계상 앞뒤가 맞는지, 문체와 어조가 "
    "일관되게 유지되는지, 문단 사이 흐름이 매끄럽게 이어지는지 평가한다. 챕터 개요"
    "(input)에서 다루기로 한 내용을 빠뜨리지 않고 자연스러운 서사로 풀어냈는지도 함께 본다."
)


async def _reconstruct_persona_gateways(persona_result: dict[str, Any]) -> tuple[Gateways, UserRecord]:
    """인터뷰(Phase 1~2)를 다시 돌리지 않고, 이미 저장된 extracted_events를 그대로
    Mock 게이트웨이에 되살린다 — 모듈 docstring 참조."""
    gateways = _build_mock_gateways()
    user = await gateways.users.create(
        UserCreateData(
            id=uuid.uuid4(),
            email=f"{persona_result['persona_id']}@evals.local",
            name=persona_result["persona_name"],
            birth_year=persona_result["birth_year"],
            hometown=persona_result["hometown"],
        )
    )
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.set_session_prose(session.id, persona_result["session_prose"])
    await gateways.sessions.complete(session.id)

    create_data = [
        EventCreateData(
            user_id=user.id,
            source_type=EventSourceType(item["source_type"]),
            session_id=session.id,
            occurred_at_label=item.get("occurred_at_label"),
            place=item.get("place"),
            people=item.get("people"),
            one_line_summary=item["one_line_summary"],
            prose_paragraph=item["prose_paragraph"],
            emotion_tag=item.get("emotion_tag"),
            emotion_intensity=item.get("emotion_intensity"),
            emotion_inferred=bool(item.get("emotion_inferred", False)),
            labels=item.get("labels") or {},
            confidence=item.get("confidence"),
            source_span=item.get("source_span"),
            life_period=LifePeriod(item["life_period"]) if item.get("life_period") else None,
            verified=True,
        )
        for item in persona_result["extracted_events"]
    ]
    events = await gateways.events.bulk_create(create_data)
    vectors = await embeddings.embed_passages([e.prose_paragraph for e in events])
    await gateways.events.bulk_update_embeddings([(e.id, v) for e, v in zip(events, vectors)])
    await gateways.commit()
    return gateways, user


async def _run_phase34(gateways: Gateways, user: UserRecord) -> dict[str, Any]:
    """Phase 3(병합·중요도·스타일 바이블) → Phase 4(목차→시놉시스→챕터 집필→통일성
    윤문)를 실제 Solar 호출로 끝까지 돌려, 실제로 독자에게 전달되는 최종 완성본
    (autobiography.final_content)을 얻는다.

    개별 챕터의 chapter.content가 아니라 final_content를 평가 대상으로 삼는 이유:
    finalize_manuscript의 통일성 윤문 패스는 인접 챕터 경계·문체를 다듬어 final_content
    에만 반영하고 챕터별 content는 그대로 둔다(autobiography_service.finalize_manuscript
    참조) — "완성된" 서사일관성을 재려면 실제로 완성된 그 결과물을 봐야 한다."""
    autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)
    autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography.id)
    autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)

    chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
    for chapter in chapters:
        await autobiography_service.write_chapter(gateways, chapter.id)

    autobiography = await autobiography_service.finalize_manuscript(gateways, autobiography.id)
    return {
        "title": autobiography.title,
        "book_synopsis": autobiography.book_synopsis,
        "final_content": autobiography.final_content,
    }


async def _score_narrative_coherence(judge: SolarJudgeModel, manuscript: dict[str, Any]) -> dict[str, Any]:
    metric = GEval(
        name="Narrative Coherence",
        criteria=_NARRATIVE_COHERENCE_CRITERIA,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=0.5,
    )
    test_case = LLMTestCase(
        input=manuscript["book_synopsis"] or manuscript["title"] or "",
        actual_output=manuscript["final_content"] or "",
    )
    await metric.a_measure(test_case, _show_indicator=False)
    return {
        "title": manuscript["title"],
        "score": metric.score,
        "reason": metric.reason,
        "success": metric.success,
    }


def _latest_results_dir() -> Path:
    candidates = [d for d in _RESULTS_DIR.iterdir() if d.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"{_RESULTS_DIR}에 결과 디렉터리가 없습니다 — 먼저 run_benchmark.py를 실행하세요.")
    return sorted(candidates, key=lambda d: d.stat().st_mtime)[-1]


async def evaluate_persona(judge: SolarJudgeModel, persona_result: dict[str, Any]) -> dict[str, Any]:
    gateways, user = await _reconstruct_persona_gateways(persona_result)
    manuscript = await _run_phase34(gateways, user)
    return await _score_narrative_coherence(judge, manuscript)


async def main(results_dir: Path | None = None) -> None:
    results_dir = results_dir or _latest_results_dir()
    persona_files = sorted(results_dir.glob("p*.json"))
    if not persona_files:
        print(f"[경고] {results_dir}에 페르소나 결과 파일이 없습니다.")
        return

    judge = SolarJudgeModel()
    all_reports: dict[str, dict[str, Any]] = {}
    for path in persona_files:
        persona_result = json.loads(path.read_text(encoding="utf-8"))
        persona_id = persona_result["persona_id"]
        print(f"[평가 중] {persona_id} ({path.name}) — Phase 3/4 재구성 중...")
        try:
            all_reports[persona_id] = await evaluate_persona(judge, persona_result)
        except Exception as exc:  # noqa: BLE001 — 페르소나 하나가 실패해도 나머지는 계속 진행
            print(f"[실패] {persona_id}: {exc!r}")

    out_path = results_dir / "narrative_coherence_report.json"
    out_path.write_text(json.dumps(all_reports, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 서사일관성 G-Eval 점수 (n={len(all_reports)}명) ===")
    for persona_id, report in all_reports.items():
        print(f"  {persona_id} / {report['title']}: {report['score']:.2f} ({'통과' if report['success'] else '미달'})")
    print(f"\n상세 결과: {out_path}")


if __name__ == "__main__":
    results_dir_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(results_dir_arg))
