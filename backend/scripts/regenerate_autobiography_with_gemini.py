"""
billgates@dummy.com 등 특정 계정의 진행 중(미완성) 자서전을 Phase 3(스타일 바이블·목차)
부터 전부 재생성하되, 집필 파이프라인(app/services/autobiography_service.py,
app/services/character_service.py)의 LLM 호출만 Solar 대신 Gemini로 라우팅해 비교해보기
위한 일회성 스크립트다(app/clients/llm_router.py 참조).

.env의 AUTOBIOGRAPHY_LLM_PROVIDER 기본값("solar")은 건드리지 않는다 — 이 스크립트
프로세스 안에서만 settings.AUTOBIOGRAPHY_LLM_PROVIDER를 "gemini"로 잠깐 덮어쓴다(런타임
값 변경이라 다른 프로세스·다음 실행에는 영향이 없다). .env에 GEMINI_API_KEY가 없으면
첫 LLM 호출에서 바로 실패한다.

Phase 3(consolidate_autobiography)는 style_bible을 통째로 새로 만들어 덮어쓰므로, 기존에
확정돼 있던 말투/구성/컨셉 커스터마이징(style_bible.customization.confirmed)이 사라진다
— 이 스크립트는 재생성 전에 그 값을 미리 읽어두었다가 Phase 3 직후 같은 값으로 다시
확정해 재적용한다(사용자가 이미 골랐던 조합을 유지). 확정된 커스터마이징이 없었던
계정이면 이 단계는 건너뛰고 기본(비커스터마이징) 목차 흐름으로 진행한다.

목차 후보는 매번 새로 생성되므로 이전에 선택했던 candidate_index를 그대로 재사용할
근거가 없다 — 기본값으로 0번(첫 후보)을 선택한다. 다른 후보를 원하면 --candidate-index로
지정한다.

챕터 집필(write_chapter)은 세션 처리 스크립트(process_seeded_sessions.py)와 동일한 이유로
챕터마다 독립된 트랜잭션(gateways_context())을 쓴다 — 챕터 하나가 실패해도 다른 챕터의
결과에 영향을 주지 않는다.

실행 방법 (backend/ 디렉토리에서):
    ..\\venv\\Scripts\\python -m scripts.regenerate_autobiography_with_gemini --email "billgates@dummy.com"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import settings

settings.AUTOBIOGRAPHY_LLM_PROVIDER = "gemini"  # 이 프로세스 안에서만 유효 — .env는 그대로 "solar".

from app.gateways.factory import gateways_context  # noqa: E402
from app.services import autobiography_service  # noqa: E402

_DEFAULT_CHAPTER_CONCURRENCY = 3
_STAGE_TIMEOUT_SECONDS = 900  # Gemini 응답 지연 대비 넉넉한 여유(Solar 기준 90~180초보다 큼).


async def _resolve_user_and_autobiography(email: str) -> tuple[uuid.UUID, uuid.UUID, dict | None]:
    async with gateways_context() as gateways:
        user = await gateways.users.get_by_email(email)
        if user is None:
            raise SystemExit(f"{email}에 해당하는 유저가 없습니다.")
        autobiography = await gateways.autobiographies.get_latest_unfinished_by_user(user.id)
        if autobiography is None:
            raise SystemExit(f"{email}에게 진행 중인(미완성) 자서전이 없습니다.")
        customization = (autobiography.style_bible or {}).get("customization")
        if not customization or not customization.get("confirmed"):
            customization = None
        return user.id, autobiography.id, customization


async def _reapply_customization(autobiography_id: uuid.UUID, customization: dict) -> None:
    confirmed = customization["confirmed"]
    async with gateways_context() as gateways:
        await autobiography_service.save_customization_selection(
            gateways,
            autobiography_id,
            tones=customization.get("tones") or [confirmed["tone"]],
            structures=customization.get("structures") or [confirmed["structure"]],
            concepts=customization.get("concepts") or [confirmed["concept"]],
        )
        await autobiography_service.confirm_customization(
            gateways,
            autobiography_id,
            tone=confirmed["tone"],
            structure=confirmed["structure"],
            concept=confirmed["concept"],
        )


async def _generate_toc_and_select(autobiography_id: uuid.UUID, candidate_index: int) -> list[uuid.UUID]:
    async with gateways_context() as gateways:
        await asyncio.wait_for(
            autobiography_service.generate_toc_candidates(gateways, autobiography_id),
            timeout=_STAGE_TIMEOUT_SECONDS,
        )
    async with gateways_context() as gateways:
        await asyncio.wait_for(
            autobiography_service.select_toc_candidate(gateways, autobiography_id, candidate_index),
            timeout=_STAGE_TIMEOUT_SECONDS,
        )
    async with gateways_context() as gateways:
        chapters = await gateways.chapters.list_by_autobiography(autobiography_id)
    return [c.id for c in chapters]


async def _write_one_chapter(chapter_draft_id: uuid.UUID) -> None:
    async with gateways_context() as gateways:
        chapter = await asyncio.wait_for(
            autobiography_service.write_chapter(gateways, chapter_draft_id),
            timeout=_STAGE_TIMEOUT_SECONDS,
        )
    print(
        f"    -> {chapter.chapter_index}장 '{chapter.title}' 집필 완료 ({len(chapter.content or '')}자)",
        file=sys.stderr,
    )


async def _write_all_chapters(chapter_ids: list[uuid.UUID], concurrency: int) -> None:
    semaphore = asyncio.Semaphore(concurrency)

    async def _worker(chapter_id: uuid.UUID) -> None:
        async with semaphore:
            await _write_one_chapter(chapter_id)

    results = await asyncio.gather(*(_worker(cid) for cid in chapter_ids), return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    for exc in failures:
        print(f"    [실패] 챕터 집필 실패: {exc!r}", file=sys.stderr)
    if failures:
        raise SystemExit(f"{len(failures)}개 챕터 집필 실패 — 위 로그를 확인하세요.")


async def _finalize(autobiography_id: uuid.UUID) -> None:
    async with gateways_context() as gateways:
        await asyncio.wait_for(
            autobiography_service.finalize_manuscript(gateways, autobiography_id),
            timeout=_STAGE_TIMEOUT_SECONDS,
        )


async def main(email: str, *, candidate_index: int, concurrency: int) -> None:
    print(f"[provider] AUTOBIOGRAPHY_LLM_PROVIDER={settings.AUTOBIOGRAPHY_LLM_PROVIDER}", file=sys.stderr)
    print(f"[1/5] {email}의 진행 중 자서전을 찾는 중...", file=sys.stderr)
    user_id, autobiography_id, customization = await _resolve_user_and_autobiography(email)
    print(f"  autobiography_id={autobiography_id}", file=sys.stderr)

    start = time.monotonic()

    print("[2/5] Phase 3(스타일 바이블·이벤트 병합·중요도) 재생성 중 (Gemini)...", file=sys.stderr)
    async with gateways_context() as gateways:
        await asyncio.wait_for(
            autobiography_service.consolidate_autobiography(gateways, user_id),
            timeout=_STAGE_TIMEOUT_SECONDS,
        )

    if customization:
        print(f"  이전 커스터마이징 재적용: {customization['confirmed']}", file=sys.stderr)
        await _reapply_customization(autobiography_id, customization)
    else:
        print("  확정된 커스터마이징 기록 없음 — 기본(비커스터마이징) 목차 흐름으로 진행", file=sys.stderr)

    print(f"[3/5] 목차 생성 및 후보 {candidate_index}번 선택 중...", file=sys.stderr)
    chapter_ids = await _generate_toc_and_select(autobiography_id, candidate_index)
    print(f"  챕터 {len(chapter_ids)}개 초안 생성됨", file=sys.stderr)

    print(f"[4/5] 챕터 {len(chapter_ids)}개 집필 중 (동시성={concurrency})...", file=sys.stderr)
    await _write_all_chapters(chapter_ids, concurrency)

    print("[5/5] 최종 통일성 윤문 중...", file=sys.stderr)
    await _finalize(autobiography_id)

    elapsed = time.monotonic() - start
    print(f"\n완료. 총 소요 시간: {elapsed / 60:.1f}분. autobiography_id={autobiography_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="지정 계정의 진행 중 자서전을 Phase 3부터 Gemini로 전부 재생성합니다."
    )
    parser.add_argument("--email", required=True, help="대상 유저 이메일")
    parser.add_argument("--candidate-index", type=int, default=0, help="선택할 목차 후보 인덱스(기본 0)")
    parser.add_argument(
        "--concurrency", type=int, default=_DEFAULT_CHAPTER_CONCURRENCY, help="챕터 동시 집필 수(기본 3)"
    )
    args = parser.parse_args()
    asyncio.run(main(args.email, candidate_index=args.candidate_index, concurrency=args.concurrency))
