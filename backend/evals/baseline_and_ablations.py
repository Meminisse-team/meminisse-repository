"""
기획안 6절 "비교 실험 설계(베이스라인 및 어블레이션)" — evals/README.md 1~3절
(합성 페르소나 벤치마크·DeepEval 라벨정확도·G-Eval 서사일관성)까지는 구현됐지만
이 항목은 전혀 손대지 않았던 상태였다(2026-07-18 확인).

evals/run_benchmark.py가 이미 저장해 둔 페르소나 결과(extracted_events, transcript,
session_prose)를 입력 삼아, "본 시스템(full)"과 대조군 4가지를 같은 재료로 돌려
최종 원고(final_content)를 얻는다. 실제 통계 비교(Wilcoxon)는
evals/baseline_ablation_comparison.py가 담당하고, 이 파일은 조건별 원고 생성만
책임진다.

조건 정의(기획안 6절):
- full: 본 시스템 그대로(동적 목차 + 이벤트 분할 + [원 인터뷰의] 꼬리 질문).
  deepeval_narrative_coherence._reconstruct_persona_gateways/_run_phase34를 그대로
  재사용한다.
- baseline: "기존 상용 서비스군의 공개된 설계를 문헌 기반으로 재구현한 고정 템플릿
  베이스라인(고정 목차 + 챕터별 할당량 생성)" — 이벤트 추출·RAG·스타일 바이블·
  검증(팩트체크/근거검증) 전부 생략하고, 원본 발화를 그대로 정해진 분량으로
  "부풀리는" 단일 Solar 호출만 수행한다. 1.1절이 분석한 경쟁사군("모두의 자서전"
  등: "사전 생성 목차에 메모를 남기면 문장을 부풀려 원고화")의 설계를 재현한 것.
- no_dynamic_toc: 동적 목차(의미론적 군집화) 제거 — 이벤트는 그대로 쓰되, 목차를
  LLM 클러스터링이 아니라 연도순 1이벤트=1챕터 고정 배치로 강제한다.
- no_event_split: 이벤트 1급 객체화 제거 — 세션에서 여러 세부 이벤트로 쪼개진
  extracted_events를 다시 하나로 합쳐(사건 병합의 역방향) Phase 3/4에 투입한다.
- no_followup: 꼬리 질문 제거 — 저장된 원 대화록(transcript)을 페르소나 첫 발화 +
  인터뷰어 첫 반응 한 턴으로 잘라, 그 잘린 대화만으로 Phase 2(재조립+이벤트 추출)를
  처음부터 다시 돌린다(이후 Phase 3/4는 full과 동일).

**중요한 스케일 한계**: 지금 페르소나(evals/personas.py)는 "세션 하나 = 사건 하나"
설계라(README 1절), no_dynamic_toc와 no_event_split 어블레이션은 이벤트가 1~6개뿐인
현재 파일럿 규모에서는 효과가 거의 드러나지 않을 수 있다 — 두 어블레이션 모두
"클러스터링/분할할 재료가 애초에 적다"는 이유로 full과 결과가 비슷하게 나올 수
있으며, 이는 어블레이션 설계 결함이 아니라 30명 규모(사용자당 여러 세션)로 늘려야
드러나는 효과다. baseline_ablation_comparison.py 결과 해석 시 반드시 이 한계를
함께 읽을 것.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.clients import solar
from app.gateways.dto import EventCreateData, EventRecord, SessionCreateData, UserCreateData, UserRecord
from app.gateways.factory import Gateways, _build_mock_gateways
from app.models.enums import EventSourceType, LifePeriod, MessageRole, SessionType
from app.services import autobiography_service, event_extraction_service
from evals.deepeval_narrative_coherence import _reconstruct_persona_gateways, _run_phase34

_BASELINE_SYSTEM_PROMPT = """\
당신은 자서전 대필 서비스의 문장 윤문기입니다. 아래는 인터뷰이가 실제로 한 말을
정리한 글입니다. 이 내용을 바탕으로, 정해진 목차 제목의 챕터 한 편을 800~1200자
분량으로 자연스러운 1인칭 산문으로 풀어 쓰세요. 원문에 없는 새로운 사건이나
인물은 지어내지 말고, 문장을 다듬고 살을 붙이는 데에만 집중하세요.
"""


async def run_full(persona_result: dict[str, Any]) -> dict[str, Any]:
    gateways, user = await _reconstruct_persona_gateways(persona_result)
    return await _run_phase34(gateways, user)


async def run_baseline(persona_result: dict[str, Any]) -> dict[str, Any]:
    """고정 템플릿 베이스라인. 이벤트 추출·동적 목차·RAG·팩트체크·근거검증 전부
    생략 — 원본 발화를 곧장 "부풀리는" 단일 LLM 호출 하나로 끝낸다(모듈 docstring
    참조)."""
    raw_text = persona_result.get("session_prose") or "\n".join(
        turn["content"] for turn in persona_result.get("transcript", []) if turn.get("role") == "user"
    )
    fixed_title = f"{persona_result.get('life_period_label', '내 이야기')}"
    response = await solar.chat_completion(
        [
            {"role": "system", "content": _BASELINE_SYSTEM_PROMPT},
            {"role": "user", "content": f"[챕터 제목] {fixed_title}\n\n[인터뷰 정리본]\n{raw_text}"},
        ],
        reasoning_effort="low",
    )
    content = response.choices[0].message.content or ""
    return {"title": persona_result.get("persona_name", "") + "의 이야기", "book_synopsis": None, "final_content": content}


async def run_ablation_no_dynamic_toc(persona_result: dict[str, Any]) -> dict[str, Any]:
    """동적 목차(LLM 의미론적 군집화)를 제거하고, 연도순 1이벤트=1챕터의 고정
    목차를 강제한다. generate_toc_candidates(LLM 클러스터링 호출)를 아예 건너뛰고
    select_toc_candidate가 기대하는 toc_data.candidates 스키마를 직접 채워
    넣는다 — select_toc_candidate 이후 단계(시놉시스·집필·팩트체크·근거검증·
    통일성 윤문)는 full과 완전히 동일한 코드 경로를 그대로 탄다."""
    gateways, user = await _reconstruct_persona_gateways(persona_result)
    return await run_no_dynamic_toc_for_user(gateways, user.id)


async def apply_fixed_chronological_toc(gateways: Gateways, autobiography_id: uuid.UUID) -> None:
    """generate_toc_candidates(LLM 의미론적 클러스터링)를 건너뛰고, 그 자리에
    연도순 1이벤트=1챕터의 결정론적 고정 목차를 직접 써 넣는다. select_toc_candidate가
    기대하는 toc_data.candidates 스키마만 채우면 그 이후(시놉시스·집필·검증·윤문)는
    손대지 않아도 full과 동일한 코드 경로를 탄다 — evals(Mock)와 실제 DB(real
    페르소나 데이터, evals/real_data_comparison.py) 양쪽에서 재사용한다."""
    autobiography = await autobiography_service.get_autobiography_by_id(gateways, autobiography_id)
    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    events.sort(key=lambda e: autobiography_service._event_estimated_year(e) or 0)

    fixed_candidate = {
        "parts": [],  # Part 구조 없음(episodic 폴백) — _normalize_toc_parts가 지원하는 형태.
        "chapters": [
            {
                "chapter_index": i + 1,
                "title": f"{i + 1}장",
                "theme_keywords": [],
                "connecting_thread": None,
                "part_index": None,
            }
            for i in range(len(events))
        ],
    }
    toc_data = {
        "generated_at": autobiography_service._now_iso(),
        "candidates": [fixed_candidate],
        "selected_candidate_index": None,
    }
    await gateways.autobiographies.update(autobiography_id, toc_data=toc_data)
    await gateways.commit()


async def _finish_phase4_from_selected_toc(
    gateways: Gateways, autobiography_id: uuid.UUID, *, chapter_concurrency: int = 1
) -> dict[str, Any]:
    """select_toc_candidate 이후 공통 꼬리(챕터 집필 → 통일성 윤문) — full과 모든
    TOC 관련 어블레이션이 공유한다.

    chapter_concurrency=1(기본값)은 기존과 동일한 순차 처리로, Mock 페르소나
    경로(run_ablation_no_dynamic_toc)가 계속 이걸 쓴다 — Mock 백엔드에서는
    evals/parallel_chapters.write_chapters_parallel을 쓰면 안 되므로(그 모듈
    docstring 참조) 기본값을 안전한 쪽으로 유지했다. 1보다 크면 실제 Postgres
    백엔드 전용 병렬 경로를 탄다(evals/real_data_comparison.py가
    chapter_concurrency>1로 호출)."""
    autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography_id, 0)
    chapter_ids = [c.id for c in await autobiography_service.list_chapter_drafts(gateways, autobiography.id)]

    if chapter_concurrency > 1:
        from evals.parallel_chapters import write_chapters_parallel
        from app.gateways.factory import gateways_context

        await write_chapters_parallel(chapter_ids, concurrency=chapter_concurrency)
        async with gateways_context() as fresh_gateways:  # 병렬 집필 이후 새 세션으로 재확정 읽기
            autobiography = await autobiography_service.finalize_manuscript(fresh_gateways, autobiography.id)
    else:
        for chapter_id in chapter_ids:
            await autobiography_service.write_chapter(gateways, chapter_id)
        autobiography = await autobiography_service.finalize_manuscript(gateways, autobiography.id)

    return {
        "title": autobiography.title,
        "book_synopsis": autobiography.book_synopsis,
        "final_content": autobiography.final_content,
    }


async def run_no_dynamic_toc_for_user(
    gateways: Gateways, user_id: uuid.UUID, *, chapter_concurrency: int = 1
) -> dict[str, Any]:
    """no_dynamic_toc 조건의 본체 — 이미 존재하는 (gateways, user_id)를 받아 그대로
    실행한다. Mock 페르소나(run_ablation_no_dynamic_toc)와 실제 DB 페르소나
    (evals/real_data_comparison.py) 양쪽이 이 함수를 공유한다."""
    autobiography = await autobiography_service.consolidate_autobiography(gateways, user_id)
    await apply_fixed_chronological_toc(gateways, autobiography.id)
    return await _finish_phase4_from_selected_toc(
        gateways, autobiography.id, chapter_concurrency=chapter_concurrency
    )


def _merge_extracted_events(extracted_events: list[dict[str, Any]]) -> dict[str, Any]:
    """세부 이벤트 여러 건을 하나로 되합친다 — 세션 하나에서 쪼개진 이벤트들을
    다시 뭉쳐 "이벤트 1급 객체화 이전" 상태를 근사한다. 병합 판정(_judge_same_event
    같은 의미 판정)이 아니라 무조건 합치는 결정론적 병합이다 — 이 어블레이션의
    목적 자체가 "분할 로직이 없었다면"이므로 의미 판정을 다시 쓰면 안 된다."""
    if len(extracted_events) == 1:
        return extracted_events[0]

    def _first_non_null(key: str) -> Any:
        return next((e.get(key) for e in extracted_events if e.get(key)), None)

    merged_labels: dict[str, Any] = {}
    for event in extracted_events:
        for key, value in (event.get("labels") or {}).items():
            if value and not merged_labels.get(key):
                merged_labels[key] = value

    return {
        "source_type": extracted_events[0]["source_type"],
        "occurred_at_label": _first_non_null("occurred_at_label"),
        "place": _first_non_null("place"),
        "people": _first_non_null("people"),
        "one_line_summary": " / ".join(e["one_line_summary"] for e in extracted_events if e.get("one_line_summary")),
        "prose_paragraph": " ".join(e["prose_paragraph"] for e in extracted_events if e.get("prose_paragraph")),
        "emotion_tag": _first_non_null("emotion_tag"),
        "emotion_intensity": max((e.get("emotion_intensity") or 0 for e in extracted_events), default=None),
        "emotion_inferred": all(e.get("emotion_inferred", False) for e in extracted_events),
        "labels": merged_labels,
        "confidence": None,
        "source_span": None,
        "life_period": _first_non_null("life_period"),
    }


async def run_ablation_no_event_split(persona_result: dict[str, Any]) -> dict[str, Any]:
    """이벤트 1급 객체화를 제거 — 세부 이벤트들을 다시 하나로 합친 뒤 그 이후는
    full과 동일한 Phase 3/4(동적 목차 포함)를 탄다. 세부 이벤트가 원래 1개뿐인
    페르소나는 병합할 게 없어 사실상 full과 동일해진다 — 결과 해석 시 모듈
    docstring의 스케일 한계를 참고할 것."""
    merged = _merge_extracted_events(persona_result["extracted_events"])
    merged_persona_result = {**persona_result, "extracted_events": [merged]}
    gateways, user = await _reconstruct_persona_gateways(merged_persona_result)
    return await _run_phase34(gateways, user)


def _event_record_to_merge_dict(event: EventRecord) -> dict[str, Any]:
    """실제 DB에서 조회한 EventRecord(dataclass, enum 필드)를 _merge_extracted_events가
    기대하는 dict 형태(문자열 enum, evals/run_benchmark.py의 JSON 덤프와 동일한 모양)로
    변환한다 — evals/real_data_comparison.py가 실제 시딩된 유명인 데이터의 이벤트를
    병합할 때 이 어댑터를 거쳐 _merge_extracted_events를 그대로 재사용한다."""
    return {
        "source_type": event.source_type.value,
        "occurred_at_label": event.occurred_at_label,
        "place": event.place,
        "people": event.people,
        "one_line_summary": event.one_line_summary,
        "prose_paragraph": event.prose_paragraph,
        "emotion_tag": event.emotion_tag,
        "emotion_intensity": event.emotion_intensity,
        "emotion_inferred": event.emotion_inferred,
        "labels": event.labels or {},
        "life_period": event.life_period.value if event.life_period else None,
    }


def merge_event_records(events: list[EventRecord]) -> dict[str, Any]:
    """실제 DB EventRecord 리스트 버전의 _merge_extracted_events — 어댑터만 거치고
    병합 규칙 자체는 완전히 동일하다(모듈 docstring 원칙 재사용)."""
    return _merge_extracted_events([_event_record_to_merge_dict(e) for e in events])


async def run_ablation_no_followup(persona_result: dict[str, Any]) -> dict[str, Any]:
    """꼬리 질문을 제거 — 저장된 실제 대화록의 첫 교환(페르소나 첫 발화 +
    인터뷰어 첫 반응)만 남기고 나머지 후속 턴은 버린 뒤, 실제 프로덕션 함수
    event_extraction_service.process_completed_session을 그 잘린 대화로 처음부터
    다시 돌린다(사설 헬퍼가 아니라 진짜 운영 코드 경로를 그대로 태우는 방식 —
    Phase 2 로직이 바뀌어도 이 어블레이션이 자동으로 최신 상태를 반영한다).
    이후 Phase 3/4는 full과 동일(동적 목차 포함)하게 진행한다."""
    gateways = _build_mock_gateways()
    user = await gateways.users.create(
        UserCreateData(
            id=uuid.uuid4(),
            email=f"{persona_result['persona_id']}-nofollowup@evals.local",
            name=persona_result["persona_name"],
            birth_year=persona_result["birth_year"],
            hometown=persona_result["hometown"],
        )
    )
    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    truncated_transcript = persona_result["transcript"][:2]  # 첫 user 턴 + 첫 assistant 턴만.
    for turn in truncated_transcript:
        role = MessageRole.USER if turn["role"] == "user" else MessageRole.ASSISTANT
        await gateways.sessions.add_chat_log(session.id, role=role, content=turn["content"])
    await gateways.sessions.complete(session.id)
    await gateways.commit()

    await event_extraction_service.process_completed_session(gateways, session.id)
    return await _run_phase34(gateways, user)


CONDITIONS: dict[str, Any] = {
    "full": run_full,
    "baseline": run_baseline,
    "no_dynamic_toc": run_ablation_no_dynamic_toc,
    "no_event_split": run_ablation_no_event_split,
    "no_followup": run_ablation_no_followup,
}
