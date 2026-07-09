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

prompts.py의 모든 build_* 함수를 이 라우터에서 커버한다(Phase 1/2뿐 아니라 Phase 3의
스타일 바이블·이벤트 병합 판정, Phase 4의 동적 목차·하향식 집필·통일성 윤문·팩트체크·
제3자 위해성 분류·NER 스캔까지 포함). 그중 build_ocr_confirmation_question과
classify_life_milestone_category는 Upstage 호출이 전혀 없는 순수 로컬 함수라
별도 표시(LLM 미호출)와 함께 미리보기 용도로만 노출한다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.agents import prompts
from app.clients import solar
from app.schemas.sandbox import (
    BookSynopsisRequest,
    BookSynopsisResponse,
    ChapterSynopsisRequest,
    ChapterSynopsisResponse,
    ChapterWritingRequest,
    ChapterWritingResponse,
    EventExtractionRequest,
    EventExtractionResponse,
    EventItemOut,
    EventMergeJudgeRequest,
    EventMergeJudgeResponse,
    FactOut,
    FactReextractionRequest,
    FactReextractionResponse,
    FollowupRequest,
    FollowupResponse,
    GenerationOverrides,
    InterviewTurnRequest,
    InterviewTurnResponse,
    LifeMilestoneClassificationRequest,
    LifeMilestoneClassificationResponse,
    NerExtractionRequest,
    NerExtractionResponse,
    OcrConfirmationQuestionRequest,
    OcrConfirmationQuestionResponse,
    OcrValidityCheckRequest,
    OcrValidityCheckResponse,
    PersonOut,
    ProseReassemblyRequest,
    ProseReassemblyResponse,
    RelationItemOut,
    SafeguardCheckRequest,
    SafeguardCheckResponse,
    SlotGatingRequest,
    SlotGatingResponse,
    StyleBibleRequest,
    StyleBibleResponse,
    ThirdPartyRiskRequest,
    ThirdPartyRiskResponse,
    TocCandidateOut,
    TocGenerationRequest,
    TocGenerationResponse,
    UnityRevisionRequest,
    UnityRevisionResponse,
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
        "POST /sandbox/style-bible": "STYLE_BIBLE_SYSTEM_PROMPT — Phase 3 화자 스타일 바이블",
        "POST /sandbox/event-merge-judge": "EVENT_MERGE_JUDGE_SYSTEM_PROMPT — Phase 3 이벤트 병합 판정",
        "POST /sandbox/toc-generation": "TOC_GENERATION_SYSTEM_PROMPT — Phase 4 동적 목차 후보 (Structured Outputs)",
        "POST /sandbox/book-synopsis": "BOOK_SYNOPSIS_SYSTEM_PROMPT — Phase 4 책 전체 시놉시스",
        "POST /sandbox/chapter-synopsis": "CHAPTER_SYNOPSIS_SYSTEM_PROMPT — Phase 4 챕터 시놉시스",
        "POST /sandbox/chapter-writing": "CHAPTER_WRITING_SYSTEM_PROMPT — Phase 4 챕터 본문 집필",
        "POST /sandbox/unity-revision": "UNITY_REVISION_SYSTEM_PROMPT — Phase 4 통일성 윤문 패스",
        "POST /sandbox/fact-reextraction": "FACT_REEXTRACTION_SYSTEM_PROMPT — Phase 4 원문 대조 팩트체크 재추출",
        "POST /sandbox/third-party-risk": "THIRD_PARTY_RISK_SYSTEM_PROMPT — 제3자 언급 위해성 분류",
        "POST /sandbox/ner-extraction": "NER_EXTRACTION_SYSTEM_PROMPT — 등장인물 NER 스캔",
        "POST /sandbox/ocr-confirmation-question": "[LLM 미호출] build_ocr_confirmation_question 미리보기",
        "POST /sandbox/life-milestone-classification": "[LLM 미호출] classify_life_milestone_category 미리보기",
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


@router.post("/style-bible", response_model=StyleBibleResponse)
async def sandbox_style_bible(payload: StyleBibleRequest) -> StyleBibleResponse:
    """app/services/autobiography_service.py의 _generate_style_bible과 동일한 호출."""
    messages = prompts.build_style_bible_prompt(all_session_prose=payload.all_session_prose)
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "medium",
        temperature=opts.temperature,
    )
    return StyleBibleResponse(messages_sent=messages, style_bible_content=response.choices[0].message.content or "")


@router.post("/event-merge-judge", response_model=EventMergeJudgeResponse)
async def sandbox_event_merge_judge(payload: EventMergeJudgeRequest) -> EventMergeJudgeResponse:
    """app/services/autobiography_service.py의 _judge_same_event와 동일한 호출.

    판정이 불확실하면 same_event=false(병합하지 않음)로 나오는 것이 정상이다 —
    과병합은 인쇄 후 회복 불가능하지만 과분리는 사용자 확인으로 즉시 회복 가능하다는
    리스크 비대칭이 기본값의 근거다(EVENT_MERGE_JUDGE_SYSTEM_PROMPT 참조).
    """
    messages = prompts.build_event_merge_judge_prompt(
        event_a_summary=payload.event_a_summary, event_b_summary=payload.event_b_summary
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="event_merge_judge",
        json_schema=prompts.EVENT_MERGE_JUDGE_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "low",
    )
    return EventMergeJudgeResponse(
        messages_sent=messages, same_event=result["same_event"], reasoning=result["reasoning"]
    )


@router.post("/toc-generation", response_model=TocGenerationResponse)
async def sandbox_toc_generation(payload: TocGenerationRequest) -> TocGenerationResponse:
    """app/services/autobiography_service.py의 generate_toc_candidates와 동일한 호출."""
    messages = prompts.build_toc_generation_prompt(
        event_summaries_with_scores=payload.event_summaries_with_scores
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="toc_generation",
        json_schema=prompts.TOC_GENERATION_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "medium",
    )
    return TocGenerationResponse(
        messages_sent=messages,
        candidates=[TocCandidateOut(**candidate) for candidate in result.get("candidates", [])],
    )


@router.post("/book-synopsis", response_model=BookSynopsisResponse)
async def sandbox_book_synopsis(payload: BookSynopsisRequest) -> BookSynopsisResponse:
    """app/services/autobiography_service.py의 _generate_book_synopsis와 동일한 호출."""
    messages = prompts.build_book_synopsis_prompt(style_bible=payload.style_bible, toc=payload.toc)
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "medium",
        temperature=opts.temperature,
    )
    return BookSynopsisResponse(messages_sent=messages, book_synopsis=response.choices[0].message.content or "")


@router.post("/chapter-synopsis", response_model=ChapterSynopsisResponse)
async def sandbox_chapter_synopsis(payload: ChapterSynopsisRequest) -> ChapterSynopsisResponse:
    """app/services/autobiography_service.py의 _generate_chapter_synopsis와 동일한 호출."""
    messages = prompts.build_chapter_synopsis_prompt(
        book_synopsis=payload.book_synopsis,
        chapter_title=payload.chapter_title,
        event_summaries=payload.event_summaries,
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "medium",
        temperature=opts.temperature,
    )
    return ChapterSynopsisResponse(
        messages_sent=messages, chapter_synopsis=response.choices[0].message.content or ""
    )


@router.post("/chapter-writing", response_model=ChapterWritingResponse)
async def sandbox_chapter_writing(payload: ChapterWritingRequest) -> ChapterWritingResponse:
    """app/services/autobiography_service.py의 _generate_chapter_content와 동일한 호출.

    하향식 집필의 마지막 단계 — 스타일 바이블·전체/챕터 시놉시스·직전 챕터 요약·
    RAG로 소환된 사건 문단을 전부 주입해 챕터 본문을 생성한다.
    """
    messages = prompts.build_chapter_writing_prompt(
        style_bible=payload.style_bible,
        book_synopsis=payload.book_synopsis,
        chapter_synopsis=payload.chapter_synopsis,
        previous_chapter_summary=payload.previous_chapter_summary,
        retrieved_event_paragraphs=payload.retrieved_event_paragraphs,
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "high",
        temperature=opts.temperature,
    )
    return ChapterWritingResponse(
        messages_sent=messages, chapter_content=response.choices[0].message.content or ""
    )


@router.post("/unity-revision", response_model=UnityRevisionResponse)
async def sandbox_unity_revision(payload: UnityRevisionRequest) -> UnityRevisionResponse:
    """app/services/autobiography_service.py의 finalize_manuscript와 동일한 호출."""
    messages = prompts.build_unity_revision_prompt(
        style_bible=payload.style_bible, full_manuscript=payload.full_manuscript
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    response = await solar.chat_completion(
        messages,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "high",
        temperature=opts.temperature,
    )
    return UnityRevisionResponse(
        messages_sent=messages, revised_manuscript=response.choices[0].message.content or ""
    )


@router.post("/fact-reextraction", response_model=FactReextractionResponse)
async def sandbox_fact_reextraction(payload: FactReextractionRequest) -> FactReextractionResponse:
    """app/services/autobiography_service.py의 _run_factcheck 1단계(재추출)와 동일한 호출.

    이후 개체 정규화·라벨 대조는 서비스 레이어의 결정론적 로컬 로직이 담당하므로
    이 샌드박스는 재추출 결과(facts)까지만 보여준다.
    """
    messages = prompts.build_fact_reextraction_prompt(chapter_content=payload.chapter_content)
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="fact_reextraction",
        json_schema=prompts.FACT_REEXTRACTION_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "low",
    )
    return FactReextractionResponse(
        messages_sent=messages, facts=[FactOut(**fact) for fact in result.get("facts", [])]
    )


@router.post("/third-party-risk", response_model=ThirdPartyRiskResponse)
async def sandbox_third_party_risk(payload: ThirdPartyRiskRequest) -> ThirdPartyRiskResponse:
    """app/services/character_service.py의 _classify_risk와 동일한 호출.

    이 분류는 가명 적용 여부를 결정하는 게이트가 아니다 — 전수 가명화 기본값(opt-out)은
    이 결과와 무관하게 항상 적용되고, 여기서는 실명 유지 시 표시할 고지문의 강도만
    조정하는 보조 신호를 산출한다.
    """
    messages = prompts.build_third_party_risk_prompt(
        person_name=payload.person_name, chapter_excerpts=payload.chapter_excerpts
    )
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="third_party_risk",
        json_schema=prompts.THIRD_PARTY_RISK_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "low",
    )
    return ThirdPartyRiskResponse(
        messages_sent=messages,
        person_name=result["person_name"],
        risk_detected=result["risk_detected"],
        risk_classification=result["risk_classification"],
        risk_reasons=result.get("risk_reasons", []),
    )


@router.post("/ner-extraction", response_model=NerExtractionResponse)
async def sandbox_ner_extraction(payload: NerExtractionRequest) -> NerExtractionResponse:
    """app/services/character_service.py의 scan_and_classify_chapter 1단계(NER 스캔)와 동일한 호출."""
    messages = prompts.build_ner_extraction_prompt(chapter_content=payload.chapter_content)
    if payload.system_prompt_override:
        messages[0] = {"role": "system", "content": payload.system_prompt_override}

    opts = payload.generation or GenerationOverrides()
    result = await solar.structured_completion(
        messages,
        schema_name="ner_extraction",
        json_schema=prompts.NER_EXTRACTION_SCHEMA,
        model=opts.model or solar.DEFAULT_MODEL,
        reasoning_effort=opts.reasoning_effort or "low",
    )
    return NerExtractionResponse(
        messages_sent=messages, people=[PersonOut(**person) for person in result.get("people", [])]
    )


@router.post("/ocr-confirmation-question", response_model=OcrConfirmationQuestionResponse)
async def sandbox_ocr_confirmation_question(
    payload: OcrConfirmationQuestionRequest,
) -> OcrConfirmationQuestionResponse:
    """[LLM 미호출] Upstage를 부르지 않는 순수 문자열 포맷팅 — media_service의 Phase 1
    OCR 검증 대기 큐 항목이 인터뷰 중 확인 질문으로 어떻게 표현되는지만 미리 본다."""
    question = prompts.build_ocr_confirmation_question(
        suspected_text=payload.suspected_text, guessed_value=payload.guessed_value
    )
    return OcrConfirmationQuestionResponse(question=question)


@router.post("/life-milestone-classification", response_model=LifeMilestoneClassificationResponse)
async def sandbox_life_milestone_classification(
    payload: LifeMilestoneClassificationRequest,
) -> LifeMilestoneClassificationResponse:
    """[LLM 미호출] Upstage를 부르지 않는 결정론적 키워드 매칭 — Phase 3 중요도 스코어링의
    생애 이정표 카테고리 매칭(LIFE_MILESTONE_KEYWORDS) 신호를 프롬프트 없이 바로 확인한다."""
    category = prompts.classify_life_milestone_category(payload.text)
    return LifeMilestoneClassificationResponse(category=category)
