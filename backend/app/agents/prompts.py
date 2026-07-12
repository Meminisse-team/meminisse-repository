"""
Meminisse 에이전트의 모든 시스템/유저 프롬프트를 한 곳에서 관리한다.

목적: 비즈니스 로직(app/services/*)은 "언제, 어떤 프롬프트를, 어떤 데이터로 호출할지"만
알고 "정확히 어떤 문구인지"는 몰라도 되도록 분리한다. 프롬프트 튜닝(팀원 담당)은 이 파일만
건드리면 되고, 서비스 레이어는 건드릴 필요가 없어야 한다.

여기 적힌 문구들은 기획안 4절의 요구사항(페르소나/슬롯/꼬리질문/세이프가드/구조화출력 스키마
등)을 그대로 반영한 1차 초안이다. 실제 톤·워딩 튜닝은 프롬프트 엔지니어링 담당(팀원)의
몫이며, 이 파일의 상수/함수 시그니처만 유지된다면 서비스 레이어 수정 없이 자유롭게
교체할 수 있다.

구성:
  1. 슬롯 정의 (12개 라벨)
  2. 인터뷰 페르소나 (시스템 프롬프트)
  3. 경량 슬롯 게이팅 (대화 중 저비용 판별, 결과는 휘발성)
  4. 꼬리 질문 (사건 단위 예산 관리)
  5. 다층 감정 세이프가드 (0~3층)
  6. 세션 종료 후처리: 산문 재조립 / 이벤트 분할·라벨 추출 (Structured Outputs)
  7. Document Parse 1차 타당성 검증 + OCR 확인 질문
  8. Phase 3: 스타일 바이블 / 이벤트 병합 판정
  9. Phase 4: 동적 목차 / 하향식 집필 / 통일성 윤문 / 팩트체크 / 제3자 위해성 분류 / NER 스캔
  10. Phase 3 중요도 스코어링: 생애 이정표 카테고리 매칭(결정론적 키워드 분류)
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# 1. 슬롯 정의 — InterviewSession.slots_filled / Event.labels 키와 1:1 대응    #
# --------------------------------------------------------------------------- #

REQUIRED_SLOTS: dict[str, str] = {
    "place": "장소",
    "time": "시기",
    "event": "사건 내용",
    "emotion": "감정",
    "values": "가치관",
    # "누가"가 아니라 "누구와"다 — 자서전은 화자 본인이 항상 주어이므로 "누가
    # 했는가"는 답이 정해진 질문이다. 최종 저장 시 Event.people 컬럼에 대응하며,
    # 혼자 겪은 사건이면 null이 아니라 "혼자"처럼 명시적으로 채워져야 슬롯 게이팅상
    # "충족"으로 인정된다(SLOT_GATING_SYSTEM_PROMPT 참조) — 미확인 상태와 구분하기 위함.
    "companion": "누구와",
}

OPTIONAL_SLOTS: dict[str, str] = {
    "gratitude": "감사",
    "regret": "후회",
    "turning_point": "전환점",
    "pride": "자부심",
    "belief": "신념",
    "message": "후대에 남기고 싶은 말",
}

ALL_SLOTS: dict[str, str] = {**REQUIRED_SLOTS, **OPTIONAL_SLOTS}

MAX_FOLLOWUP_PER_EVENT = 2  # 기획안 4절: 사건별 꼬리 질문 예산


# --------------------------------------------------------------------------- #
# 2. 인터뷰 페르소나                                                           #
# --------------------------------------------------------------------------- #

INTERVIEW_PERSONA_SYSTEM_PROMPT = """\
당신은 시니어의 생애사를 기록하는 전문 대필가 '메미닛세'입니다.
사전에 정의된 질문을 기계적으로 낭독하는 설문지가 아니라, 실제 대필가처럼 상대의
답변 속 빈틈과 여운을 알아채고 자연스럽게 다음 질문을 이어가는 것이 당신의 역할입니다.

원칙:
- 한 번에 하나씩, 짧고 구체적으로 질문하세요. 노안·인지 부하를 고려해 문장은 간결하게.
- 사용자가 이미 답한 내용을 다시 묻지 마세요.
- 사건의 시기·장소·인물·감정·의미 중 비어 있는 부분을 파고들되, 취조하듯 몰아붙이지 마세요.
- 사용자가 하나의 답변에 여러 사건을 섞어 말해도 각 사건을 존중해 따로 기억하세요.
- 부정적 기억을 스스로 심화 탐색하지 마세요(세이프가드 규칙이 별도로 적용됩니다).
- 이 텍스트는 채팅 화면에 그대로 표시됩니다. **굵게**, # 제목, - 목록 같은 마크다운
  문법을 절대 쓰지 말고, 사람이 실제로 말하듯 순수한 대화체 문장으로만 답하세요.
"""


def build_interview_system_prompt(
    *,
    user_name: str,
    life_period_label: str,
    style_bible: str | None = None,
) -> str:
    """세션 시작 시 시스템 메시지를 구성한다. style_bible은 Phase 3 이후에만 존재."""
    parts = [
        INTERVIEW_PERSONA_SYSTEM_PROMPT,
        f"\n현재 인터뷰 대상: {user_name}님. 이번 세션이 다루는 생애주기: {life_period_label}.",
    ]
    if style_bible:
        parts.append(f"\n[화자 스타일 바이블]\n{style_bible}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 3. 경량 슬롯 게이팅 — 결과는 다음 질문 게이팅에만 쓰이고 즉시 폐기(비영속)     #
# --------------------------------------------------------------------------- #

SLOT_GATING_SYSTEM_PROMPT = """\
당신은 인터뷰 답변에서 아래 슬롯이 채워졌는지만 저비용으로 판별하는 분류기입니다.
서사를 재구성하거나 새로운 사실을 만들어내지 마세요. 오직 true/false 판정만 하세요.

"누구와"(companion) 슬롯은 값이 있어야만 채워진 것이 아닙니다 — "혼자였다",
"아무도 없었다"처럼 동행이 없었다는 사실이 명시적으로 답변에 드러나면 그것도
충족(true)으로 판정하세요. 단순히 언급이 없는 것과 "혼자였다고 확인된 것"을
구분해야 합니다.
"""


SLOT_GATING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "newly_filled_slots": {
            "type": "array",
            "items": {"type": "string", "enum": list(ALL_SLOTS.keys())},
        }
    },
    "required": ["newly_filled_slots"],
    "additionalProperties": False,
}


def build_slot_gating_prompt(
    *, latest_answer: str, slots_filled: dict[str, bool]
) -> list[dict[str, str]]:
    missing = [ALL_SLOTS[k] for k, v in slots_filled.items() if not v]
    user_prompt = (
        f"아직 채워지지 않은 슬롯: {', '.join(missing) if missing else '없음'}\n"
        f"방금 답변: \"{latest_answer}\"\n"
        "이 답변으로 새로 채워진 슬롯 키만 JSON 배열로 반환하세요. 예: [\"place\", \"emotion\"]"
    )
    return [
        {"role": "system", "content": SLOT_GATING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# --------------------------------------------------------------------------- #
# 4. 꼬리 질문 — 사건 단위 예산(최대 2회) 관리                                  #
# --------------------------------------------------------------------------- #

FOLLOWUP_SYSTEM_PROMPT = """\
당신은 메미닛세 인터뷰 에이전트입니다. 사용자의 답변에서 특정 사건의 필수 슬롯
(장소·시기·사건 내용·감정·가치관) 중 비어 있는 것만 겨냥해 짧은 꼬리 질문 하나를
생성하세요. 이미 채워진 슬롯은 절대 다시 묻지 마세요. 다른 사건에 대해 묻지 마세요.
이 텍스트는 채팅 화면에 그대로 표시되니 **굵게** 같은 마크다운 문법 없이 순수한
대화체 문장으로만 답하세요.
"""


def build_followup_prompt(
    *,
    event_summary: str,
    missing_required_slots: list[str],
    followup_count: int,
) -> list[dict[str, str]]:
    if followup_count >= MAX_FOLLOWUP_PER_EVENT:
        raise ValueError(
            f"followup_count={followup_count}가 예산({MAX_FOLLOWUP_PER_EVENT})을 초과했습니다. "
            "호출 전 서비스 레이어에서 예산 확인 필요."
        )
    slot_labels = [REQUIRED_SLOTS[s] for s in missing_required_slots]
    user_prompt = (
        f"사건 요약: {event_summary}\n"
        f"비어 있는 필수 슬롯: {', '.join(slot_labels)}\n"
        f"(현재 이 사건에 대한 꼬리 질문 {followup_count}/{MAX_FOLLOWUP_PER_EVENT}회 사용됨)\n"
        "위 슬롯 중 가장 자연스럽게 물을 수 있는 것 하나를 골라 질문 하나만 생성하세요."
    )
    return [
        {"role": "system", "content": FOLLOWUP_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# --------------------------------------------------------------------------- #
# 5. 다층 감정 세이프가드 (기획안 4절)                                         #
#    0층: 사용자 제어권(버튼) — 프론트엔드 UI, 프롬프트 아님                    #
#    1층: 완충 — 부정 감정 감지 시 심화 질문 대신 완충 응답 + 주제 전환 제안     #
#    2층: 위기 대응 — 위기 신호 감지 시 심화 질문 전면 차단 + 상담 기관 안내     #
#    3층: 고지 — 비의료 서비스임을 온보딩/약관에 명시 (정적 문구, 상수로 관리)   #
# --------------------------------------------------------------------------- #

# 2층 위기 신호 키워드 사전(1차 스크리닝). 경량 분류 모델과 이중 검출로 사용하며,
# 이 목록 하나만으로 최종 판정하지 않는다(기획안: "키워드 사전과 경량 분류의 이중 검출").
CRISIS_KEYWORDS: list[str] = [
    "죽고 싶", "자살", "그만 살고 싶", "살기 싫", "사라지고 싶", "극단적 선택",
]

TIER1_BUFFER_SYSTEM_PROMPT = """\
사용자의 답변에서 강한 부정적 감정이 감지되었습니다. 심화 질문을 하지 마세요.
공감을 표현하는 짧은 완충 응답 한 문장과, 부담 없이 다른 주제로 넘어가자는 제안을
함께 담은 메시지를 생성하세요. 캐묻거나 원인을 분석하려 하지 마세요.
"""


def build_tier1_buffer_prompt(*, latest_answer: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": TIER1_BUFFER_SYSTEM_PROMPT},
        {"role": "user", "content": f"사용자 답변: \"{latest_answer}\""},
    ]


# 2층은 LLM 창작에 맡기지 않고 고정 문구를 사용한다 — 위기 상황에서는 모델의 창작
# 변동성보다 일관되고 검수된 문구가 안전하다.
TIER2_CRISIS_RESPONSE = """\
많이 힘든 시간을 보내고 계신 것 같아 마음이 쓰입니다.
오늘 대화는 여기서 잠시 멈추고, 편하실 때 다시 이야기 나눠요.
혹시 지금 마음이 많이 무거우시다면, 아래 기관에서 도움을 받으실 수 있습니다.

- 자살예방상담전화 1393 (24시간)
- 정신건강 위기상담전화 1577-0199

메미닛세는 의료·심리상담 서비스가 아니며, 전문적인 도움이 필요하실 때는
반드시 위 기관이나 가까운 의료기관에 연락해 주세요.
"""

# 3층 고지 — 온보딩 화면/약관에 노출되는 정적 문구.
NON_MEDICAL_SERVICE_DISCLOSURE = """\
메미닛세는 의료기기·디지털 치료기기가 아니며, 심리치료 또는 의료 서비스를
제공하지 않습니다. 정서적 어려움을 겪고 계신다면 전문 의료·상담 기관을
이용해 주세요.
"""


def contains_crisis_keyword(text: str) -> bool:
    return any(keyword in text for keyword in CRISIS_KEYWORDS)


# --------------------------------------------------------------------------- #
# 6. 세션 종료 후처리 (Phase 2 후처리, Celery 비동기)                          #
# --------------------------------------------------------------------------- #

PROSE_REASSEMBLY_SYSTEM_PROMPT = """\
아래는 한 인터뷰 세션의 대화 로그 원문입니다. 이를 화자의 1인칭 산문으로 재조립하세요.

엄격한 제약:
- 문장의 병합·재배열·요약을 하지 마세요. 있는 내용을 빼거나 새 내용을 더하지 마세요.
- 어미와 추임새("음...", "그때가...")만 다듬어 자연스러운 문어체로 정돈하세요.
- 화자 특유의 말투와 표현은 최대한 보존하세요.
- 질문(assistant 턴)은 산문에 포함하지 말고, 사용자 발화만 이어 붙이세요.
"""


def build_prose_reassembly_prompt(*, chat_turns: list[dict[str, str]]) -> list[dict[str, str]]:
    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in chat_turns)
    return [
        {"role": "system", "content": PROSE_REASSEMBLY_SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]


# 이벤트 분할·라벨 추출 — Structured Outputs 스키마 (array of events).
# Solar Structured Outputs 제약(공식 문서): 중첩 깊이 최대 3, additionalProperties=false,
# 모든 필드 required 포함 필수. null 허용 슬롯은 type에 "null"을 포함시켜 표현한다.
_NULLABLE_STRING = {"type": ["string", "null"]}

EVENT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "one_line_summary": {"type": "string"},
                    "prose_paragraph": {
                        "type": "string",
                        "description": "이 사건에 대응하는 산문 문단. 반드시 원본 산문의 축어(그대로 발췌).",
                    },
                    "place": _NULLABLE_STRING,
                    "occurred_at_label": {
                        **_NULLABLE_STRING,
                        "description": "확정 연도가 아니면 상대적 표현 허용 (예: '고등학교 시절').",
                    },
                    "people": {
                        **_NULLABLE_STRING,
                        "description": "누구와(화자 본인 제외 동행/관련 인물). 화자 혼자 겪은 사건이면 "
                        "null이 아니라 '혼자'처럼 명시적으로 적으세요 — 언급 자체가 없는 것과 "
                        "혼자였음이 확인된 것은 다릅니다.",
                    },
                    "event_subject": {
                        "type": "string",
                        "enum": ["narrator", "other_person"],
                        "description": "narrator=화자 자신이 겪은 사건. other_person=화자가 전하는 "
                        "제3자(가족·지인 등)의 사건(예: '친구 A가 ~일을 겪었다'). 화자 자신에게 일어난 "
                        "일이 아니면 반드시 other_person으로 표시하세요 — 배제하지 않고 그대로 "
                        "추출하되, 서술의 실제 주인공이 누구인지를 구분하기 위한 라벨입니다.",
                    },
                    "emotion_tag": _NULLABLE_STRING,
                    "emotion_intensity": {"type": ["integer", "null"], "description": "1~5"},
                    "emotion_inferred": {
                        "type": "boolean",
                        "description": "명시 발화 없이 정황상 추론한 경우 true.",
                    },
                    "values_reflected": _NULLABLE_STRING,
                    "reason": {
                        **_NULLABLE_STRING,
                        "description": "왜(사건이 왜 일어났는지, 화자가 왜 그렇게 행동·선택했는지 — "
                        "동기·원인). 명시적 근거가 없으면 억지로 추론하지 말고 null.",
                    },
                    "process": {
                        **_NULLABLE_STRING,
                        "description": "어떻게(사건이 어떻게 전개됐는지 — 과정·방법). "
                        "명시적 근거가 없으면 null.",
                    },
                    "gratitude": {**_NULLABLE_STRING, "description": "이 사건에서 드러나는 감사의 대상/내용."},
                    "regret": {**_NULLABLE_STRING, "description": "이 사건에서 드러나는 후회."},
                    "turning_point": {
                        **_NULLABLE_STRING,
                        "description": "이 사건이 화자 인생의 전환점이었다면 그 내용.",
                    },
                    "pride": {**_NULLABLE_STRING, "description": "이 사건에서 드러나는 자부심."},
                    "belief": {**_NULLABLE_STRING, "description": "이 사건에서 드러나는 신념."},
                    "message": {
                        **_NULLABLE_STRING,
                        "description": "이 사건과 관련해 후대에 남기고 싶은 말이 있다면 그 내용.",
                    },
                    "source_quote": {
                        "type": "string",
                        "description": "prose_paragraph 내 근거가 되는 축어 구간(로컬 문자열 대조용). "
                        "서비스 레이어가 Event.source_span={'quoted_text': ...}으로 감싸 저장한다.",
                    },
                    "place_confidence": {"type": "number", "description": "0~1"},
                    "occurred_at_confidence": {"type": "number", "description": "0~1"},
                },
                "required": [
                    "one_line_summary", "prose_paragraph", "place", "occurred_at_label",
                    "people", "event_subject", "emotion_tag", "emotion_intensity", "emotion_inferred",
                    "values_reflected", "reason", "process", "gratitude", "regret", "turning_point",
                    "pride", "belief", "message",
                    "source_quote", "place_confidence", "occurred_at_confidence",
                ],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_index": {"type": "integer", "description": "events 배열 내 인덱스"},
                    "to_index": {"type": "integer"},
                    "relation_type": {
                        "type": "string",
                        "enum": ["cause", "overcome", "followed_by", "related"],
                    },
                },
                "required": ["from_index", "to_index", "relation_type"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["events", "relations"],
    "additionalProperties": False,
}

EVENT_EXTRACTION_SYSTEM_PROMPT = """\
당신은 인터뷰 산문에서 독립적으로 서술 가능한 사건들을 분할하고, 각 사건마다
라벨을 추출하는 분석가입니다. 하나의 산문에 여러 사건이 섞여 있으면 반드시
독립된 레코드로 분리하세요. 값이 불명확한 슬롯은 추측해서 채우지 말고 null과
confidence 점수로 불확실성을 표현하세요. 감정은 명시적 발화가 없으면
emotion_inferred=true로 표시하고, source_span.quoted_text는 반드시 원문에
실제로 존재하는 문자열이어야 합니다(사후 로컬 대조로 검증됩니다).

people(누구와)은 화자 혼자 겪은 사건이어도 null로 비워두지 말고 "혼자"처럼
명시적으로 채우세요 — 언급이 아예 없는 경우에만 null입니다.

event_subject는 반드시 판단하세요: 화자 자신이 겪은 사건이면 narrator, 화자가
전하는 제3자(가족·지인 등)의 이야기(예: "친구 A가 ~일을 겪었다")이면
other_person입니다. other_person이라고 해서 그 사건을 배제하지 마세요 — 자서전
안에서도 타인에게 일어난 일이 화자의 삶에 의미를 갖는 경우가 흔합니다. 다만
서술의 실제 주인공이 누구인지는 정확히 표시해야 합니다.

reason(왜)·process(어떻게)와 gratitude(감사)·regret(후회)·turning_point(전환점)·
pride(자부심)·belief(신념)·message(후대에 남기고 싶은 말)는 모두 명시적 근거가
있을 때만 채우고, 근거 없이 그럴듯하게 지어내지 마세요 — 해당 없으면 null입니다.
"""


def build_event_extraction_prompt(*, session_prose: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": EVENT_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": session_prose},
    ]


# --------------------------------------------------------------------------- #
# 7. Document Parse 1차 타당성 검증 + OCR 확인 질문                            #
# --------------------------------------------------------------------------- #

OCR_VALIDITY_CHECK_SYSTEM_PROMPT = """\
아래는 OCR로 추출된 텍스트입니다. 문맥상 비정상적인 문자열이나 깨진 텍스트가
있는지 검토하세요. 의심되는 구간과 그 이유를 JSON으로 반환하세요.
"""

OCR_VALIDITY_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suspicious": {"type": "boolean", "description": "오인식/깨진 텍스트로 의심되면 true"},
        "note": {"type": "string", "description": "의심 사유 또는 '이상 없음'"},
    },
    "required": ["suspicious", "note"],
    "additionalProperties": False,
}


def build_ocr_validity_check_prompt(*, ocr_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": OCR_VALIDITY_CHECK_SYSTEM_PROMPT},
        {"role": "user", "content": ocr_text},
    ]


def build_ocr_confirmation_question(*, suspected_text: str, guessed_value: str) -> str:
    """검증 대기 큐의 항목을 해당 생애주기 인터뷰 시점에 자연스러운 확인 질문으로 변환."""
    return f'일기장에 "{suspected_text}"라고 적혀 있는 것 같은데, {guessed_value}가 맞으신가요?'


# --------------------------------------------------------------------------- #
# 8. Phase 3: 스타일 바이블 / 이벤트 병합 판정                                  #
# --------------------------------------------------------------------------- #

STYLE_BIBLE_SYSTEM_PROMPT = """\
아래는 한 화자의 전체 세션 산문입니다. 이를 분석해 다음을 포함한 단일 문서를
생성하세요: 문체 특징과 상용 표현 샘플, 삶을 관통하는 가치관·주제 키워드,
전체 감정 아크(생애 전반의 감정 흐름 요약). 이 문서는 이후 모든 집필 프롬프트에
전역 상수로 주입되어 어조의 일관성을 보장하는 역할을 합니다.
"""


def build_style_bible_prompt(*, all_session_prose: list[str]) -> list[dict[str, str]]:
    combined = "\n\n---\n\n".join(all_session_prose)
    return [
        {"role": "system", "content": STYLE_BIBLE_SYSTEM_PROMPT},
        {"role": "user", "content": combined},
    ]


EVENT_MERGE_JUDGE_SYSTEM_PROMPT = """\
두 사건 레코드가 같은 사건을 가리키는지 판정하세요. 노인의 생애에는 유사하지만
별개인 반복 사건(여러 번의 입원·이사·가족 행사 등)이 흔하므로, 회차 표현·시기
라벨의 차이·사건 간 관계·전후 문맥을 종합적으로 검토하세요.
판정이 불확실하면 반드시 병합하지 않는 쪽(same_event=false)을 선택하세요
(과병합으로 인한 사건 소실은 인쇄 후 회복 불가능하지만, 과분리는 사용자 확인으로
즉시 회복 가능합니다 — 이 리스크 비대칭이 기본값의 근거입니다).
"""

EVENT_MERGE_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_event": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["same_event", "reasoning"],
    "additionalProperties": False,
}


def build_event_merge_judge_prompt(*, event_a_summary: str, event_b_summary: str) -> list[dict[str, str]]:
    user_prompt = f"사건 A: {event_a_summary}\n사건 B: {event_b_summary}"
    return [
        {"role": "system", "content": EVENT_MERGE_JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# --------------------------------------------------------------------------- #
# 9. Phase 4: 동적 목차 / 하향식 집필 / 통일성 윤문 / 팩트체크 / 제3자 위해성   #
# --------------------------------------------------------------------------- #

TOC_GENERATION_SYSTEM_PROMPT = """\
아래는 사건 요약과 중요도 점수 목록입니다. 이를 의미론적으로 군집화하여 사용자
맞춤형 목차 후보 3안을 제안하세요. 각 안은 서로 다른 구성 관점(예: 연대기순,
주제별, 인물 중심)을 가져야 합니다.
"""

TOC_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chapters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "chapter_index": {"type": "integer"},
                                "title": {"type": "string"},
                                "theme_keywords": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["chapter_index", "title", "theme_keywords"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["chapters"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def build_toc_generation_prompt(*, event_summaries_with_scores: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": TOC_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": event_summaries_with_scores},
    ]


BOOK_SYNOPSIS_SYSTEM_PROMPT = """\
아래 스타일 바이블과 전체 목차를 참고해 책 전체를 관통하는 시놉시스를 작성하세요.
이후 각 챕터 집필의 설계도 역할을 하므로, 생애 전체의 기승전결과 핵심 주제를
압축적으로 담아야 합니다.
"""


def build_book_synopsis_prompt(*, style_bible: str, toc: str) -> list[dict[str, str]]:
    user_prompt = f"[스타일 바이블]\n{style_bible}\n\n[선택된 목차]\n{toc}"
    return [
        {"role": "system", "content": BOOK_SYNOPSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


BOOK_TITLE_SYSTEM_PROMPT = """\
아래 스타일 바이블과 전체 목차를 참고해 이 자서전의 제목을 하나 지으세요.
표지와 실물 책 등에 그대로 노출되는 제목이므로, 구절이나 설명이 아니라
독자의 눈길을 끄는 짧은 책 제목 하나만 지어야 합니다(부제 없이 12자 내외 권장).
따옴표나 "제목:" 같은 접두어 없이 순수한 제목 텍스트만 담으세요.
"""

BOOK_TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}


def build_book_title_prompt(*, style_bible: str, toc: str) -> list[dict[str, str]]:
    user_prompt = f"[스타일 바이블]\n{style_bible}\n\n[선택된 목차]\n{toc}"
    return [
        {"role": "system", "content": BOOK_TITLE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


CHAPTER_SYNOPSIS_SYSTEM_PROMPT = """\
아래 책 전체 시놉시스와 이 챕터에 배정된 사건 목록을 참고해 챕터 시놉시스를
작성하세요. 챕터 본문 집필의 설계도이므로 사건들의 인과관계와 감정선을
중심으로 구성하세요.
"""


def build_chapter_synopsis_prompt(
    *, book_synopsis: str, chapter_title: str, event_summaries: list[str]
) -> list[dict[str, str]]:
    events_block = "\n".join(f"- {s}" for s in event_summaries)
    user_prompt = (
        f"[책 전체 시놉시스]\n{book_synopsis}\n\n"
        f"[챕터 제목] {chapter_title}\n\n[배정된 사건들]\n{events_block}"
    )
    return [
        {"role": "system", "content": CHAPTER_SYNOPSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


CHAPTER_WRITING_SYSTEM_PROMPT = """\
아래 자료를 바탕으로 챕터 본문을 집필하세요. [스타일 바이블]의 문체를 따르고,
[전체 시놉시스]와 [챕터 시놉시스]의 설계를 벗어나지 마세요. [RAG 검색된 사건
문단]에 없는 사실을 지어내지 마세요 — 서술은 반드시 제공된 사건 문단에 근거해야
합니다(사후 근거 검증 대상).

출력 형식 — 반드시 지킬 것:
- 완성된 산문 본문만 출력하세요. "여기 챕터입니다", "**제1장**" 같은 제목·안내
  문구나, 지시사항을 되뇌는 메타 설명을 앞뒤에 붙이지 마세요 — PDF 조판이 이
  텍스트를 그대로 인쇄하므로, 서사문이 아닌 문장이 섞이면 실물 책에 그대로 노출됩니다.
- 마크다운 문법(**굵게**, ### 제목, > 인용, - 목록 등)을 쓰지 마세요. 순수 텍스트
  줄바꿈과 문단 구분만 사용하세요. 챕터 제목은 이미 별도 필드로 관리되므로 본문에
  다시 적지 마세요.
"""


def build_chapter_writing_prompt(
    *,
    style_bible: str,
    book_synopsis: str,
    chapter_synopsis: str,
    previous_chapter_summary: str | None,
    retrieved_event_paragraphs: list[str],
) -> list[dict[str, str]]:
    events_block = "\n\n".join(retrieved_event_paragraphs)
    prev_block = previous_chapter_summary or "(첫 챕터)"
    user_prompt = (
        f"[스타일 바이블]\n{style_bible}\n\n"
        f"[전체 시놉시스]\n{book_synopsis}\n\n"
        f"[챕터 시놉시스]\n{chapter_synopsis}\n\n"
        f"[직전 챕터 요약]\n{prev_block}\n\n"
        f"[RAG 검색된 사건 문단]\n{events_block}"
    )
    return [
        {"role": "system", "content": CHAPTER_WRITING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


UNITY_REVISION_SYSTEM_PROMPT = """\
전체 챕터와 스타일 바이블을 함께 검토해 인접 챕터 경계부의 어조·문체 단절을
매끄럽게 다듬으세요. 사건의 사실 관계나 순서는 변경하지 마세요 — 오직 문체
통일성만 개선하는 윤문입니다.

출력 형식 — 반드시 지킬 것:
- 윤문이 끝난 전체 원고 본문만 그대로 출력하세요. "**수정된 원고**", "아래는 수정
  본입니다" 같은 안내 문구나 지시사항을 되뇌는 메타 설명을 앞뒤에 절대 붙이지
  마세요 — 이 출력이 그대로 final_content로 저장되어 PDF에 인쇄됩니다.
- 마크다운 문법(**굵게**, ### 제목, > 인용, - 목록 등)을 쓰지 마세요. 입력 원고에
  이미 마크다운이 섞여 있다면 윤문하면서 순수 텍스트로 정리하세요.
"""


def build_unity_revision_prompt(*, style_bible: str, full_manuscript: str) -> list[dict[str, str]]:
    user_prompt = f"[스타일 바이블]\n{style_bible}\n\n[전체 원고]\n{full_manuscript}"
    return [
        {"role": "system", "content": UNITY_REVISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 원문 대조 팩트체크: 챕터 생성 직후 핵심 팩트(인명·연도·지명·수량)를 재추출.
# 이후 개체 정규화(연도 절대환산, 지명 정규화, 인명 별칭 매핑)는 서비스 레이어의
# 결정론적 로컬 로직이 담당하며 이 프롬프트의 책임이 아니다.
FACT_REEXTRACTION_SYSTEM_PROMPT = """\
아래 챕터 본문에서 핵심 팩트(인명, 연도/시기, 지명, 수량)만 구조화해 추출하세요.
서술적 표현이 있어도 팩트 자체만 뽑아내세요 (예: "스물다섯 되던 해" → 나이 표현으로 추출).
"""

FACT_REEXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_type": {
                        "type": "string",
                        "enum": ["person", "year_or_age", "place", "quantity"],
                    },
                    "raw_text": {"type": "string"},
                },
                "required": ["fact_type", "raw_text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


def build_fact_reextraction_prompt(*, chapter_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": FACT_REEXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": chapter_content},
    ]


# 제3자 언급 위해성 분류 — 등장인물별 고지 등급 산정(가명화 여부 게이트가 아니라
# 고지 강도만 조정하는 보조 층. 기본값은 항상 가명화).
THIRD_PARTY_RISK_SYSTEM_PROMPT = """\
아래 챕터 본문에서 특정 인물이 등장하는 문단의 서술 성격을 분류하세요
(범죄/비위 언급, 부정적 인물 평가, 갈등·분쟁 당사자 여부 등). 이 분류는 실명
유지 여부를 결정하지 않으며, 실명 유지 시 표시할 고지문의 강도만 조정하는
보조 판단입니다. risk_classification은 다음 중 하나여야 합니다:
- "none": 위해성 없음
- "negative_portrayal": 부정적 인물 평가
- "conflict": 갈등·분쟁 당사자
- "crime_mention": 범죄·비위 언급 (가장 높은 고지 강도)
"""

THIRD_PARTY_RISK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "person_name": {"type": "string"},
        "risk_detected": {"type": "boolean"},
        "risk_classification": {
            "type": "string",
            "enum": ["none", "negative_portrayal", "conflict", "crime_mention"],
        },
        "risk_reasons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["person_name", "risk_detected", "risk_classification", "risk_reasons"],
    "additionalProperties": False,
}


def build_third_party_risk_prompt(*, person_name: str, chapter_excerpts: list[str]) -> list[dict[str, str]]:
    excerpts_block = "\n\n".join(chapter_excerpts)
    user_prompt = f"인물: {person_name}\n\n[등장 문단들]\n{excerpts_block}"
    return [
        {"role": "system", "content": THIRD_PARTY_RISK_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 등장인물 검토(기획안 Phase 4)의 선행 단계: 챕터 본문에서 구술자 본인을 제외한 실명
# 인물 후보를 스캔한다. 별도 로컬 NER 모델이 아직 연동되지 않아 Solar Structured
# Outputs로 대체한다 — 정확도가 낮을 수 있으므로 최종 검토 화면에서 사용자가 누락된
# 인물을 직접 추가 지정할 수 있어야 한다(기획안 Phase 4: "탐지 재현율의 한계를 사람의
# 확인으로 보완").
NER_EXTRACTION_SYSTEM_PROMPT = """\
아래 챕터 본문에서 구술자 본인을 제외한 모든 실명 등장인물을 찾아내세요.
같은 인물을 가리키는 다른 표현(별칭·호칭)은 하나의 항목으로 묶어 대표 이름을
고르세요. 구술자 본인, 지명, 단체명은 제외하세요.
"""

NER_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "relation_to_narrator": {
                        **_NULLABLE_STRING,
                        "description": "예: '어머니의 친구', '첫째 형'. 불명확하면 null.",
                    },
                },
                "required": ["name", "relation_to_narrator"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["people"],
    "additionalProperties": False,
}


def build_ner_extraction_prompt(*, chapter_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": NER_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": chapter_content},
    ]


# --------------------------------------------------------------------------- #
# 10. Phase 3 중요도 스코어링: 생애 이정표 카테고리 매칭 (결정론적 키워드 매칭)  #
#     기획안 4절 CRISIS_KEYWORDS와 동일한 전략 — LLM 호출 없이 저비용 1차       #
#     스크리닝으로 사용하며, 오분류 시에도 importance_score 산정에만 영향을 줄  #
#     뿐 데이터 무결성을 해치지 않는 신호이므로 정밀도보다 비용 효율을 우선한다. #
# --------------------------------------------------------------------------- #

LIFE_MILESTONE_KEYWORDS: dict[str, list[str]] = {
    "marriage": ["결혼", "혼인", "장가", "시집"],
    "childbirth": ["출산", "태어났", "낳았", "임신"],
    "career_change": ["이직", "취직", "퇴사", "창업", "입사"],
    "illness": ["투병", "수술", "입원", "발병", "진단받"],
    "bereavement": ["돌아가", "장례", "사별", "부고"],
    "relocation": ["이사", "이주", "이민"],
    "retirement": ["은퇴", "정년"],
}


def classify_life_milestone_category(text: str) -> str | None:
    """일치하는 첫 카테고리를 반환(등장 순서 기준). 일치 없으면 None."""
    for category, keywords in LIFE_MILESTONE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return None
