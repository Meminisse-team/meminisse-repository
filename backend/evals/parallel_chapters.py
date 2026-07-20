"""
챕터 집필 병렬화 — 실제 Postgres 백엔드 전용. 실제 프로덕션 프론트엔드가 전
챕터를 Promise.all로 동시에 큐잉하는 것과 동일한 방식을, 지금까지 순차로
돌던 evals/real_data_comparison.py의 조건 생성에도 적용한다.

**추가 배경(2026-07-19, "전체 5시간 이내" 시간 제약에 대한 대응)**: 지금까지
evals/deepeval_narrative_coherence._run_phase34(및 evals/baseline_and_
ablations._finish_phase4_from_selected_toc)가 챕터를 `for chapter in chapters:
await write_chapter(...)`로 순차 처리했다 — 챕터 12~20개 × 챕터당 여러 LLM
호출(시놉시스는 이미 select_toc_candidate에서 병렬 완료, 본문 집필+팩트체크+
근거검증+수리루프)이 전부 직렬로 쌓여, 5조건 × 30명을 5시간 안에 끝내는 게
현실적으로 불가능했다. 이 모듈이 그 병목을 없앤다.

**Mock 백엔드에서는 절대 쓰지 말 것** — app/gateways/factory.gateways_context()는
GATEWAY_BACKEND=mock일 때 호출마다 완전히 새로운(비어 있는) 인메모리 스토어를
만든다(_build_mock_gateways). 이 모듈의 핵심 전제("gateways_context()를 여러 번
열어도 같은 실제 DB를 가리킨다")가 Mock에서는 성립하지 않는다 — 합성 페르소나
경로(evals/baseline_and_ablations.py의 Mock 호출부, evals/deepeval_narrative_
coherence.py)는 계속 기존 순차 루프(_run_phase34, chapter_concurrency=1)를
쓴다. 이 모듈은 evals/real_data_comparison.py·evals/real_followup_simulation.py
(항상 실제 Postgres, GATEWAY_BACKEND=postgres 고정)에서만 호출된다.

**트랜잭션 분리 이유**: 챕터마다 독립된 gateways_context()(=독립된 DB
커넥션/트랜잭션)로 write_chapter를 실행해야 진짜로 동시 처리된다 — SQLAlchemy
AsyncSession 하나를 여러 코루틴이 동시에 쓰는 건 안전하지 않다. finalize_
manuscript도 병렬 집필 이후 반드시 "새 세션"으로 다시 읽어야 한다 —
AsyncSessionLocal이 expire_on_commit=False로 설정돼 있어(app/database.py),
집필 시작 전에 이미 로드된 챕터 객체가 다른 세션의 커밋을 자동으로 반영하지
않을 수 있기 때문이다(재현하진 않았지만 이론적으로 SQLAlchemy identity map이
그 시점의 캐시된 값을 돌려줄 위험 — 확실한 안전을 위해 finalize 직전에 항상
새 세션을 연다).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.gateways.factory import gateways_context
from app.services import autobiography_service

# 실측 없이 잡은 보수적 기본값 — Upstage 요청 한도(429)를 감안해 너무 높이지
# 않았다. 실제 환경에서 429가 잦으면 낮추고, 여유가 있으면 올려도 된다
# (evals/real_data_comparison.py의 --chapter-concurrency로 조정).
DEFAULT_CHAPTER_CONCURRENCY = 4


async def write_chapters_parallel(chapter_ids: list[uuid.UUID], *, concurrency: int) -> None:
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(chapter_id: uuid.UUID) -> None:
        async with semaphore:
            async with gateways_context() as gateways:
                await autobiography_service.write_chapter(gateways, chapter_id)
                await gateways.commit()

    await asyncio.gather(*(_one(cid) for cid in chapter_ids))


async def run_phase34_parallel(
    user_id: uuid.UUID, *, chapter_concurrency: int = DEFAULT_CHAPTER_CONCURRENCY
) -> dict[str, Any]:
    """evals.deepeval_narrative_coherence._run_phase34의 실제 DB 전용 병렬
    버전 — consolidate~select_toc_candidate까지는 트랜잭션 하나로, 챕터 집필은
    write_chapters_parallel로 동시에, finalize_manuscript는 새 트랜잭션으로
    분리한다(모듈 docstring "트랜잭션 분리 이유" 참조). 미리 열어둔 gateways를
    받지 않고 user_id만 받는다 — 내부에서 단계별로 독립 트랜잭션을 관리하는 게
    이 함수의 핵심이라, 호출부가 트랜잭션 경계에 관여할 여지를 아예 없앴다."""
    async with gateways_context() as gateways:
        autobiography = await autobiography_service.consolidate_autobiography(gateways, user_id)
        autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography.id)
        autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)
        autobiography_id = autobiography.id
        chapter_ids = [c.id for c in await autobiography_service.list_chapter_drafts(gateways, autobiography_id)]

    await write_chapters_parallel(chapter_ids, concurrency=chapter_concurrency)

    async with gateways_context() as gateways:
        autobiography = await autobiography_service.finalize_manuscript(gateways, autobiography_id)
        return {
            "title": autobiography.title,
            "book_synopsis": autobiography.book_synopsis,
            "final_content": autobiography.final_content,
        }
