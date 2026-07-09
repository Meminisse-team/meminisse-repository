"""
프롬프트 튜닝 샌드박스 — DB/S3 없이 Upstage Solar API만 실제로 호출하는 개발자용 엔드포인트.

목적: 프롬프트 엔지니어링 담당 팀원이 app/agents/prompts.py의 문구를 고친 뒤, Swagger UI
(`/docs`)에서 바로 이 엔드포인트들을 호출해 실제 solar-pro3 응답을 확인할 수 있게 한다.
별도의 프론트엔드나 인증, DB 세션이 전혀 필요 없다 — 이 프로젝트에는 아직 인증 계층 자체가
없으므로(어떤 라우터도 Security/OAuth2 의존성을 걸지 않음) 별도 조치 없이 이미 무인증으로
접근 가능하며, 이 라우터는 그 특성을 그대로 유지하기 위해 GatewaysDep조차 요구하지 않는다
(DB를 안 만져도 되는 프롬프트 튜닝 목적에 DB 의존성을 넣는 것 자체가 불필요한 결합이다).

각 엔드포인트는 대응되는 app/services/*.py 실제 파이프라인이 호출하는 것과 동일한
prompts.py 빌더 함수 + solar 클라이언트를 그대로 사용한다 — 즉 이 샌드박스가 통과하면
실제 서비스 코드도 같은 프롬프트로 동작한다는 것이 보장된다(로직 이원화 없음).

Phase 3/4 프롬프트(스타일 바이블, 목차 생성, 챕터 집필 등)는 아직 이를 호출하는 서비스
레이어 자체가 없어(이벤트 병합·중요도 산정·하향식 집필 오케스트레이션 미구현) 여기 포함하지
않았다. 해당 서비스가 만들어지면 아래와 동일한 패턴(빌더 호출 → solar 호출 → 결과 반환)으로
몇 분 안에 추가할 수 있다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.agents import prompts
from app.clients import solar
from app.schemas.sandbox import (
    EventExtractionRequest,
    EventExtractionResponse,
    EventItemOut,
    FollowupRequest,
    FollowupResponse,
    GenerationOverrides,
    InterviewTurnRequest,
    InterviewTurnResponse,
    OcrValidityCheckRequest,
    OcrValidityCheckResponse,
    ProseReassemblyRequest,
    ProseReassemblyResponse,
    RelationItemOut,
    SafeguardCheckRequest,
    SafeguardCheckResponse,
    SlotGatingRequest,
    SlotGatingResponse,
)

router = APIRouter(prefix="/sandbox", tags=["sandbox (dev-only, no auth)"])


@router.get("")
async def list_sandbox_scenarios() -> dict[str, str]:
    """이 라우터가 지원하는 시나리오와, 각각이 튜닝하는 prompts.py 상수를 한눈에 보여준다."""
    return {
        "POST /sandbox/interview-turn": "INTERVIEW_PERSONA_SYSTEM_PROMPT — 다음 질문 생성",
        "POST /sandbox/slot-gating": "SLOT_GATING_SYSTEM_PROMPT — 슬롯 충족 여부 판별",
        "POST /sandbox/followup": "FOLLOWUP_SYSTEM_PROMPT — 사건 단위 꼬리 질문",
        "POST /sandbox/safeguard-check": "TIER1_BUFFER_SYSTEM_PROMPT / TIER2_CRISIS_RESPONSE — 감정 세이프가드",
        "POST /sandbox/prose-reassembly": "PROSE_REASSEMBLY_SYSTEM_PROMPT — 대화 로그 → 1인칭 산문",
        "POST /sandbox/event-extraction": "EVENT_EXTRACTION_SYSTEM_PROMPT — 이벤트 1급 객체화 (Structured Outputs)",
        "POST /sandbox/ocr-validity-check": "OCR_VALIDITY_CHECK_SYSTEM_PROMPT — Document Parse 결과 1차 검증",
        "참고": "모든 요청의 system_prompt_override 필드에 임시 문구를 넣으면 prompts.py를 "
        "고치지 않고도 즉시 다른 워딩으로 비교 테스트할 수 있습니다.",
    }


@router.post("/interview-turn", response_model=InterviewTurnResponse)
async def sandbox_interview_turn(payload: InterviewTurnRequest) -> InterviewTurnResponse:
    system_prompt = payload.system_prompt_override or prompts.build_interview_system_prompt(
        user_name=payload.user_name,
        life_period_label=payload.life_period_label,
        style_bible=payload.style_bible,
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": turn.role, "content": turn.content} for turn in payload.chat_history]
    messages.append({"role": "user", "content": payload.latest_user_message})

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort,
        temperature=opts.temperature,
    )
    return InterviewTurnResponse(
        system_prompt_used=system_prompt,
        messages_sent=messages,
        assistant_reply=response.choices[0].message.content or "",
        model_used=response.model,
    )


@router.post("/slot-gating", response_model=SlotGatingResponse)
async def sandbox_slot_gating(payload: SlotGatingRequest) -> SlotGatingResponse:
    messages = prompts.build_slot_gating_prompt(
        latest_answer=payload.latest_answer, slots_filled=payload.slots_filled
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="slot_gating",
        json_schema=prompts.SLOT_GATING_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "low",
    )
    return SlotGatingResponse(messages_sent=messages, newly_filled_slots=result.get("newly_filled_slots", []))


@router.post("/followup", response_model=FollowupResponse)
async def sandbox_followup(payload: FollowupRequest) -> FollowupResponse:
    try:
        messages = prompts.build_followup_prompt(
            event_summary=payload.event_summary,
            missing_required_slots=payload.missing_required_slots,
            followup_count=payload.followup_count,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort,
        temperature=opts.temperature,
    )
    return FollowupResponse(messages_sent=messages, followup_question=response.choices[0].message.content or "")


@router.post("/safeguard-check", response_model=SafeguardCheckResponse)
async def sandbox_safeguard_check(payload: SafeguardCheckRequest) -> SafeguardCheckResponse:
    crisis_detected = prompts.contains_crisis_keyword(payload.latest_answer)
    if crisis_detected:
        # 2층은 항상 고정 문구 — LLM 호출 없음 (prompts.py 8절 주석 참조).
        return SafeguardCheckResponse(
            tier="tier2_crisis",
            crisis_keyword_matched=True,
            response_text=prompts.TIER2_CRISIS_RESPONSE,
            messages_sent=None,
        )

    messages = prompts.build_tier1_buffer_prompt(latest_answer=payload.latest_answer)
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort,
        temperature=opts.temperature,
    )
    return SafeguardCheckResponse(
        tier="tier1_buffer",
        crisis_keyword_matched=False,
        response_text=response.choices[0].message.content or "",
        messages_sent=messages,
    )


@router.post("/prose-reassembly", response_model=ProseReassemblyResponse)
async def sandbox_prose_reassembly(payload: ProseReassemblyRequest) -> ProseReassemblyResponse:
    messages = prompts.build_prose_reassembly_prompt(
        chat_turns=[turn.model_dump() for turn in payload.chat_turns]
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "low",
        temperature=opts.temperature,
    )
    return ProseReassemblyResponse(messages_sent=messages, session_prose=response.choices[0].message.content or "")


@router.post("/event-extraction", response_model=EventExtractionResponse)
async def sandbox_event_extraction(payload: EventExtractionRequest) -> EventExtractionResponse:
    """기획안 핵심 설계 '이벤트 1급 객체화'의 Structured Outputs 파이프라인을 그대로 시연한다.

    app/services/event_extraction_service.py가 실제로 호출하는 것과 동일한
    build_event_extraction_prompt + EVENT_EXTRACTION_SCHEMA + structured_completion을 쓴다.
    """
    messages = prompts.build_event_extraction_prompt(session_prose=payload.session_prose)
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="event_extraction",
        json_schema=prompts.EVENT_EXTRACTION_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "medium",
    )
    return EventExtractionResponse(
        messages_sent=messages,
        events=[EventItemOut(**item) for item in result.get("events", [])],
        relations=[RelationItemOut(**item) for item in result.get("relations", [])],
    )


@router.post("/ocr-validity-check", response_model=OcrValidityCheckResponse)
async def sandbox_ocr_validity_check(payload: OcrValidityCheckRequest) -> OcrValidityCheckResponse:
    messages = prompts.build_ocr_validity_check_prompt(ocr_text=payload.ocr_text)
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="ocr_validity_check",
        json_schema=prompts.OCR_VALIDITY_CHECK_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "low",
    )
    return OcrValidityCheckResponse(
        messages_sent=messages, suspicious=result["suspicious"], note=result["note"]
    )
