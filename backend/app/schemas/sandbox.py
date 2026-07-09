"""
`app/api/v1/sandbox.py`의 요청/응답 계약.

이 스키마들은 app/schemas/{user,interview,...}.py(실 서비스 API 계약)와 별개다 — 사용자를
DB에 실제로 만들지 않고, prompts.py의 프롬프트 빌더 함수를 그대로 불러와 Upstage Solar에
1회 호출하고 결과를 즉시 보여주기 위한 전용 계약이다.

모든 요청에 공통으로 있는 `system_prompt_override`: prompts.py 안의 해당 시스템 프롬프트
상수 대신 이 문자열을 그대로 사용한다. prompts.py를 고쳐서 서버가 자동 리로드되길
기다리지 않고, Swagger 입력창에 바로 문구를 붙여넣어 즉시 결과를 비교해볼 수 있게 하기
위한 필드다(팀원이 파일을 건드리지 않고도 워딩만 빠르게 실험할 수 있음).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents import prompts


class GenerationOverrides(BaseModel):
    """모델/추론 강도/온도를 시나리오 기본값 대신 강제로 지정하고 싶을 때만 채운다."""

    model: str | None = Field(None, description="미지정 시 solar-pro3(app.clients.solar.DEFAULT_MODEL) 사용")
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    temperature: float | None = None


class ChatTurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


# --------------------------------------------------------------------------- #
# 1. 인터뷰 페르소나 — 다음 질문 생성                                           #
# --------------------------------------------------------------------------- #


class InterviewTurnRequest(BaseModel):
    user_name: str = Field("테스트 사용자", examples=["김옥순"])
    life_period_label: str = Field("성인기", examples=["유년기 (1950년대)"])
    style_bible: str | None = Field(None, description="Phase 3 이후에만 존재하는 화자 스타일 요약. 없으면 생략")
    chat_history: list[ChatTurnIn] = Field(default_factory=list, description="지금까지의 대화 턴(과거 발화)")
    latest_user_message: str = Field(..., examples=["그때 부산에서 살았어요."])
    system_prompt_override: str | None = Field(
        None, description="INTERVIEW_PERSONA_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class InterviewTurnResponse(BaseModel):
    system_prompt_used: str
    messages_sent: list[dict[str, Any]]
    assistant_reply: str
    model_used: str


# --------------------------------------------------------------------------- #
# 2. 슬롯 게이팅                                                               #
# --------------------------------------------------------------------------- #


class SlotGatingRequest(BaseModel):
    latest_answer: str = Field(..., examples=["그때 정말 기뻤어요, 아들이 태어난 날이었거든요."])
    slots_filled: dict[str, bool] = Field(
        default_factory=lambda: {key: False for key in prompts.ALL_SLOTS},
        description=f"현재까지 채워진 슬롯 상태. 키는 {list(prompts.ALL_SLOTS.keys())} 중에서.",
    )
    system_prompt_override: str | None = Field(None, description="SLOT_GATING_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class SlotGatingResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    newly_filled_slots: list[str]


# --------------------------------------------------------------------------- #
# 3. 꼬리 질문                                                                 #
# --------------------------------------------------------------------------- #


class FollowupRequest(BaseModel):
    event_summary: str = Field(..., examples=["아들이 태어난 날의 기억"])
    missing_required_slots: list[str] = Field(
        ..., examples=[["place", "emotion"]], description=f"{list(prompts.REQUIRED_SLOTS.keys())} 중에서"
    )
    followup_count: int = Field(0, ge=0, description=f"이미 사용한 꼬리 질문 횟수 (예산: {prompts.MAX_FOLLOWUP_PER_EVENT})")
    system_prompt_override: str | None = Field(None, description="FOLLOWUP_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class FollowupResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    followup_question: str


# --------------------------------------------------------------------------- #
# 4. 다층 감정 세이프가드 (1층 완충 / 2층 위기 대응)                             #
# --------------------------------------------------------------------------- #


class SafeguardCheckRequest(BaseModel):
    latest_answer: str = Field(..., examples=["요즘 다 부질없고 그만 살고 싶어요."])
    system_prompt_override: str | None = Field(None, description="TIER1_BUFFER_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class SafeguardCheckResponse(BaseModel):
    tier: Literal["tier1_buffer", "tier2_crisis"]
    crisis_keyword_matched: bool
    response_text: str
    messages_sent: list[dict[str, Any]] | None = Field(
        None, description="tier2는 고정 문구라 LLM 호출이 없어 None"
    )


# --------------------------------------------------------------------------- #
# 5. 세션 산문 재조립                                                          #
# --------------------------------------------------------------------------- #


class ProseReassemblyRequest(BaseModel):
    chat_turns: list[ChatTurnIn] = Field(..., min_length=1)
    system_prompt_override: str | None = Field(None, description="PROSE_REASSEMBLY_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class ProseReassemblyResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    session_prose: str


# --------------------------------------------------------------------------- #
# 6. 이벤트 분할·라벨 추출 (Structured Outputs, 이벤트 1급 객체화의 핵심)         #
# --------------------------------------------------------------------------- #


class EventExtractionRequest(BaseModel):
    session_prose: str = Field(
        ...,
        examples=[
            "저는 1978년에 부산에서 태어났습니다. 아버지는 어부셨고 어머니는 시장에서 장사를 하셨어요. "
            "고등학교 때 서울로 유학을 왔는데, 그때 정말 외롭고 힘들었습니다."
        ],
    )
    system_prompt_override: str | None = Field(None, description="EVENT_EXTRACTION_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class EventItemOut(BaseModel):
    one_line_summary: str
    prose_paragraph: str
    place: str | None
    occurred_at_label: str | None
    people: str | None
    emotion_tag: str | None
    emotion_intensity: int | None
    emotion_inferred: bool
    values_reflected: str | None
    source_quote: str
    place_confidence: float
    occurred_at_confidence: float


class RelationItemOut(BaseModel):
    from_index: int
    to_index: int
    relation_type: Literal["cause", "overcome", "followed_by", "related"]


class EventExtractionResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    events: list[EventItemOut]
    relations: list[RelationItemOut]


# --------------------------------------------------------------------------- #
# 7. Document Parse 결과 1차 타당성 검증                                       #
# --------------------------------------------------------------------------- #


class OcrValidityCheckRequest(BaseModel):
    ocr_text: str = Field(..., examples=["나는 1978년 3짐 15일에 태어났따."])
    system_prompt_override: str | None = Field(
        None, description="OCR_VALIDITY_CHECK_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class OcrValidityCheckResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    suspicious: bool
    note: str


# --------------------------------------------------------------------------- #
# 8. Phase 3 — 스타일 바이블 생성                                              #
# --------------------------------------------------------------------------- #


class StyleBibleRequest(BaseModel):
    all_session_prose: list[str] = Field(
        ..., min_length=1, examples=[["저는 1978년 부산에서 태어났습니다...", "학창 시절엔 조용한 아이였어요..."]]
    )
    system_prompt_override: str | None = Field(None, description="STYLE_BIBLE_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class StyleBibleResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    style_bible_content: str


# --------------------------------------------------------------------------- #
# 9. Phase 3 — 이벤트 병합 판정                                                #
# --------------------------------------------------------------------------- #


class EventMergeJudgeRequest(BaseModel):
    event_a_summary: str = Field(..., examples=["1990년 첫째 아이 출산 (서울)"])
    event_b_summary: str = Field(..., examples=["1990년경 첫 아이를 낳음 (서울 소재 병원)"])
    system_prompt_override: str | None = Field(
        None, description="EVENT_MERGE_JUDGE_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class EventMergeJudgeResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    same_event: bool
    reasoning: str


# --------------------------------------------------------------------------- #
# 10. Phase 4 — 동적 목차 생성 (Structured Outputs)                            #
# --------------------------------------------------------------------------- #


class TocGenerationRequest(BaseModel):
    event_summaries_with_scores: str = Field(
        ...,
        examples=[
            "- [중요도 12.5] 부산 출생 (시기: 1978년, 감정: 미상)\n"
            "- [중요도 9.2] 첫 취업 (시기: 2001년, 감정: 설렘)"
        ],
    )
    system_prompt_override: str | None = Field(None, description="TOC_GENERATION_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class TocChapterOut(BaseModel):
    chapter_index: int
    title: str
    theme_keywords: list[str]


class TocCandidateOut(BaseModel):
    chapters: list[TocChapterOut]


class TocGenerationResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    candidates: list[TocCandidateOut]


# --------------------------------------------------------------------------- #
# 11. Phase 4 — 책 전체 시놉시스                                                #
# --------------------------------------------------------------------------- #


class BookSynopsisRequest(BaseModel):
    style_bible: str = Field(..., examples=["간결하고 담담한 문체. 가족과 성실함을 중시함."])
    toc: str = Field(..., examples=["1. 어린 시절 (유년기)\n2. 청춘의 방황 (청년기)"])
    system_prompt_override: str | None = Field(None, description="BOOK_SYNOPSIS_SYSTEM_PROMPT 대신 사용할 임시 문구")
    generation: GenerationOverrides | None = None


class BookSynopsisResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    book_synopsis: str


# --------------------------------------------------------------------------- #
# 12. Phase 4 — 챕터 시놉시스                                                  #
# --------------------------------------------------------------------------- #


class ChapterSynopsisRequest(BaseModel):
    book_synopsis: str = Field(..., examples=["부산에서 태어나 성실하게 삶을 일군 한 사람의 이야기."])
    chapter_title: str = Field(..., examples=["1장. 어린 시절"])
    event_summaries: list[str] = Field(..., examples=[["부산 출생", "초등학교 입학"]])
    system_prompt_override: str | None = Field(
        None, description="CHAPTER_SYNOPSIS_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class ChapterSynopsisResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    chapter_synopsis: str


# --------------------------------------------------------------------------- #
# 13. Phase 4 — 챕터 본문 집필 (하향식 집필의 최종 단계)                        #
# --------------------------------------------------------------------------- #


class ChapterWritingRequest(BaseModel):
    style_bible: str = Field(..., examples=["간결하고 담담한 문체."])
    book_synopsis: str = Field(..., examples=["부산에서 태어나 성실하게 삶을 일군 한 사람의 이야기."])
    chapter_synopsis: str = Field(..., examples=["유년기의 평온함과 가족의 따뜻함을 그린다."])
    previous_chapter_summary: str | None = Field(None, description="직전 챕터 요약. 첫 챕터면 생략")
    retrieved_event_paragraphs: list[str] = Field(
        ..., min_length=1, examples=[["저는 1978년 부산에서 태어났습니다."]]
    )
    system_prompt_override: str | None = Field(
        None, description="CHAPTER_WRITING_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class ChapterWritingResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    chapter_content: str


# --------------------------------------------------------------------------- #
# 14. Phase 4 — 통일성 윤문 패스                                                #
# --------------------------------------------------------------------------- #


class UnityRevisionRequest(BaseModel):
    style_bible: str = Field(..., examples=["간결하고 담담한 문체."])
    full_manuscript: str = Field(..., examples=["[1장. 어린 시절]\n저는 부산에서 태어났습니다..."])
    system_prompt_override: str | None = Field(
        None, description="UNITY_REVISION_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class UnityRevisionResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    revised_manuscript: str


# --------------------------------------------------------------------------- #
# 15. Phase 4 — 원문 대조 팩트체크 (재추출)                                     #
# --------------------------------------------------------------------------- #


class FactReextractionRequest(BaseModel):
    chapter_content: str = Field(..., examples=["저는 1978년 부산에서 태어나 스물다섯 되던 해 서울로 왔습니다."])
    system_prompt_override: str | None = Field(
        None, description="FACT_REEXTRACTION_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class FactOut(BaseModel):
    fact_type: Literal["person", "year_or_age", "place", "quantity"]
    raw_text: str


class FactReextractionResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    facts: list[FactOut]


# --------------------------------------------------------------------------- #
# 16. 제3자 언급 위해성 분류                                                    #
# --------------------------------------------------------------------------- #


class ThirdPartyRiskRequest(BaseModel):
    person_name: str = Field(..., examples=["김철수"])
    chapter_excerpts: list[str] = Field(..., min_length=1, examples=[["김철수와 크게 다툰 적이 있다."]])
    system_prompt_override: str | None = Field(
        None, description="THIRD_PARTY_RISK_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class ThirdPartyRiskResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    person_name: str
    risk_detected: bool
    risk_classification: Literal["none", "negative_portrayal", "conflict", "crime_mention"]
    risk_reasons: list[str]


# --------------------------------------------------------------------------- #
# 17. 등장인물 NER 스캔                                                        #
# --------------------------------------------------------------------------- #


class NerExtractionRequest(BaseModel):
    chapter_content: str = Field(..., examples=["김철수와 함께 학교를 다녔다. 그의 어머니도 종종 뵈었다."])
    system_prompt_override: str | None = Field(
        None, description="NER_EXTRACTION_SYSTEM_PROMPT 대신 사용할 임시 문구"
    )
    generation: GenerationOverrides | None = None


class PersonOut(BaseModel):
    name: str
    relation_to_narrator: str | None


class NerExtractionResponse(BaseModel):
    messages_sent: list[dict[str, Any]]
    people: list[PersonOut]


# --------------------------------------------------------------------------- #
# 18. (LLM 미호출) OCR 확인 질문 문구 미리보기                                  #
# --------------------------------------------------------------------------- #


class OcrConfirmationQuestionRequest(BaseModel):
    suspected_text: str = Field(..., examples=["1975년 부산"])
    guessed_value: str = Field(..., examples=["1975년에 부산에 사신 것"])


class OcrConfirmationQuestionResponse(BaseModel):
    question: str


# --------------------------------------------------------------------------- #
# 19. (LLM 미호출) 생애 이정표 카테고리 키워드 분류 미리보기                     #
# --------------------------------------------------------------------------- #


class LifeMilestoneClassificationRequest(BaseModel):
    text: str = Field(..., examples=["1990년에 첫째 아이를 출산했다."])


class LifeMilestoneClassificationResponse(BaseModel):
    category: str | None = Field(None, description="LIFE_MILESTONE_KEYWORDS 중 일치한 첫 카테고리. 없으면 null")
