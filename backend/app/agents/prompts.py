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
(장소·시기·사건 내용·감정·가치관·누구와) 중 비어 있는 것을 겨냥해 짧은 꼬리 질문을
생성하세요. 이미 채워진 슬롯은 절대 다시 묻지 마세요. 다른 사건에 대해 묻지 마세요.

원칙적으로 한 번에 슬롯 하나만 겨냥한 질문 하나를 만드세요 — 여러 개를 한꺼번에
캐물으면 취조하듯 느껴집니다. 단 하나의 예외: "장소"와 "시기"가 둘 다 비어 있으면
이 둘은 원래 짝을 이루는 정보이므로 자연스럽게 한 문장으로 함께 물어도 됩니다
(예: "그건 언제, 어디서 있었던 일인가요?") — 두 번에 나눠 물을 필요 없습니다.

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
    combine_hint = (
        "\n'장소'와 '시기'가 둘 다 목록에 있으니, 이 둘은 한 문장으로 함께 물으세요."
        if "place" in missing_required_slots and "time" in missing_required_slots
        else ""
    )
    user_prompt = (
        f"사건 요약: {event_summary}\n"
        f"비어 있는 필수 슬롯: {', '.join(slot_labels)}\n"
        f"(현재 이 사건에 대한 꼬리 질문 {followup_count}/{MAX_FOLLOWUP_PER_EVENT}회 사용됨)\n"
        "위 슬롯 중 자연스럽게 물을 수 있는 것으로 질문 하나를 생성하세요."
        f"{combine_hint}"
    )
    return [
        {"role": "system", "content": FOLLOWUP_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 필수 슬롯 5개가 다 채워졌더라도, 이 사건에 대해 사용자가 실제로 쓴 글자 수 총합이
# 이 값에 못 미치면 "슬롯은 찼지만 자서전에 담기엔 너무 담백하다"고 보고 꼬리 질문
# 예산 안에서 한 번 더 구체화를 요청한다(MAX_FOLLOWUP_PER_EVENT와 예산을 공유하므로
# 무한히 캐묻지는 않는다). 카카오톡처럼 짧게 주고받는 채팅 UI가 사용자의 답변을
# 심리적으로 짧게 만든다는 피드백(2026-07-14)에 대한 대응 — 잠정값이며 실사용
# 데이터로 캘리브레이션 전까지의 어림값이다.
MIN_RICH_ANSWER_LENGTH = 80

ELABORATION_SYSTEM_PROMPT = """\
당신은 메미닛세 인터뷰 에이전트입니다. 사용자가 방금 이 사건의 핵심 정보(장소·
시기·사건 내용·감정·가치관)는 다 알려주었지만, 자서전에 담기엔 문장이 너무
짧고 담백합니다. 빠진 정보를 캐묻지 말고, 그 장면을 더 생생하게 떠올릴 수 있게
감각적 디테일이나 그때의 속마음을 한 가지만 자연스럽게 물어보세요(예: 그 순간의
표정·주변 풍경·몸으로 느낀 감각, 그 뒤에 스쳐간 생각 등). 취조하듯 여러 개를
한꺼번에 묻지 말고 질문 하나만 짧게 던지세요. 이 텍스트는 채팅 화면에 그대로
표시되니 마크다운 문법 없이 순수한 대화체 문장으로만 답하세요.
"""


def build_elaboration_prompt(*, user_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": ELABORATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"방금 답변: \"{user_content}\"\n"
                "이 사건에 대해 조금 더 생생하게 들려달라고 자연스럽게 청해보세요."
            ),
        },
    ]


# 슬롯도 다 차고 답변도 충분히 풍부해진 뒤, 마무리 확인(WRAP_UP_CHECK_IN_MESSAGE)
# 전에 한 번 — 필수 정보 확인(슬롯)이나 길이 확인(풍부함)과는 다른 종류의 판단이다.
# 실제 대필가라면 자연스럽게 캐물었을 만한 지점(스치듯 언급된 인물·사건·감정,
# 설명되지 않은 인과관계, 여운이 남는 대목)이 있는지 LLM이 스스로 읽고 판단한다
# (2026-07-15 피드백 — INTERVIEW_PERSONA_SYSTEM_PROMPT가 애초에 표방한 "설문지가
# 아니라 대필가처럼 빈틈을 알아채는" 역할이 슬롯/길이 판정만으로는 구현되지 않았음).
CONTEXTUAL_FOLLOWUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_followup": {"type": "boolean"},
        "question": {"type": ["string", "null"]},
    },
    "required": ["has_followup", "question"],
    "additionalProperties": False,
}

CONTEXTUAL_FOLLOWUP_SYSTEM_PROMPT = """\
당신은 시니어의 생애사를 기록하는 전문 대필가 '메미닛세'입니다. 사전에 정의된
질문을 기계적으로 확인하는 설문지가 아니라, 실제 대필가처럼 상대의 답변 속
빈틈과 여운을 알아채고 자연스럽게 다음 질문을 이어가는 것이 당신의 역할입니다.

이 사건의 필수 정보(장소·시기·사건 내용·감정·가치관)는 이미 다 채워졌습니다.
지금 할 일은 그것과 별개로, 지금까지 나눈 대화 속에 좋은 전기 작가라면 자연스럽게
더 캐물었을 만한 지점(예: 스치듯 언급된 인물·사건·감정, 설명되지 않은 인과관계,
여운이 남는 대목)이 있는지 판단하는 것입니다.

- 그런 지점이 있으면 has_followup=true로, 그 지점을 자연스럽게 파고드는 질문
  하나를 question에 담으세요.
- 없으면(이미 충분히 다뤄졌거나 더 캐물을 만한 여지가 없으면) has_followup=false로
  하고 question은 null로 두세요 — 없는데 억지로 만들어내면 안 됩니다. 애매하면
  false를 고르세요(과도한 캐묻기가 더 큰 문제입니다).
- 질문은 취조하듯 여러 개를 한꺼번에 묻지 말고 하나만, 짧고 자연스러운 대화체로
  쓰세요. 마크다운 문법은 쓰지 마세요.
"""


def build_contextual_followup_prompt(*, chat_turns: list[dict[str, str]]) -> list[dict[str, str]]:
    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in chat_turns)
    return [
        {"role": "system", "content": CONTEXTUAL_FOLLOWUP_SYSTEM_PROMPT},
        {"role": "user", "content": f"지금까지 나눈 대화:\n{transcript}"},
    ]


# 필수 슬롯도 다 채워지고 답변도 충분히 풍부해졌을 때, 곧바로 다음 질문으로
# 넘어가지 않고 이 일화에 대해 더 하고 싶은 이야기가 있는지 한 번은 확인한다
# (2026-07-15 피드백 — 세션당 한 번만 묻고, 매번 문구가 달라질 필요는 없는
# 단순한 확인 질문이라 Solar 호출 없이 고정 문구로 둔다).
WRAP_UP_CHECK_IN_MESSAGE = "혹시 이 이야기에 대해 더 들려주고 싶은 게 있으신가요? 없으면 다음 이야기로 넘어갈게요."


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

# 1층 트리거 판정 — 슬롯 게이팅(3절)과 같은 "경량 게이팅" 패턴. 2층 위기 키워드처럼
# 고정 사전으로 판별하기엔 "강한 부정적 감정"의 표현이 너무 다양해(키워드 나열이
# 사실상 불가능) 저비용 LLM 분류로 대체한다. 위기 신호는 contains_crisis_keyword가
# 먼저 걸러내므로(add_user_turn에서 이 판정보다 항상 먼저 실행) 여기서는 그보다
# 약하지만 심화 질문은 피해야 할 수준의 감정만 true로 본다.
TIER1_DETECTION_SYSTEM_PROMPT = """\
당신은 인터뷰 답변에 심화 질문을 이어가도 괜찮을지 저비용으로 판별하는 분류기입니다.

기준은 "부정적인 사건을 언급했는가"가 아니라 "지금 이 답변에서 화자가 감당하기
버거울 만큼 강렬한 고통을 표현하고 있는가"입니다. 인생 이야기에는 크고 작은
부정적 사건이 늘 섞여 있으므로, 부정적 내용이 있다는 이유만으로 true로 판정하면
안 됩니다.

true(강한 부정적 감정 — 심화 질문을 피해야 함)의 예:
- 사별, 이혼, 중대한 질병·사고, 심각한 트라우마처럼 삶을 뒤흔든 사건을 이야기하며
  현재도 고통·상실감·죄책감이 뚜렷하게 묻어남
- "너무 힘들다", "지금도 그 생각만 하면 숨이 막힌다"처럼 현재형의 격한 고통 표현
- 자기 비하나 절망감이 반복적으로 강하게 드러남

false(정상적인 서술 — 심화 질문을 계속 진행해도 됨)의 예:
- "아빠에게 혼난 적이 있다", "시험에 떨어졌다", "친구와 다퉜다"처럼 누구나 겪는
  일상적 회고 — 담담하게 서술되면 부정적 사건이라도 false
- 과거의 아쉬움이나 후회를 담담히 돌아보는 어조
- 부정적 사건을 언급하되 이미 극복했거나 웃으며 회상하는 어조

판단이 애매하면 false로 판정해 대화 흐름을 끊지 마세요 — 과도한 개입이 더 큰
문제입니다. 자살/자해를 암시하는 위기 신호는 이미 별도 경로에서 걸러지므로
여기서는 판단하지 않습니다.
"""

TIER1_DETECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"strong_negative_emotion": {"type": "boolean"}},
    "required": ["strong_negative_emotion"],
    "additionalProperties": False,
}


def build_tier1_detection_prompt(*, latest_answer: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": TIER1_DETECTION_SYSTEM_PROMPT},
        {"role": "user", "content": f"답변: \"{latest_answer}\""},
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
- assistant 턴은 어떤 형태든(질문뿐 아니라 "말씀해주셔서 감사해요", "다음 이야기로
  넘어가 볼까요?" 같은 인터뷰 진행자의 맞장구·감사 인사·화제 전환 멘트까지 전부)
  산문 결과물에 그대로 옮겨 쓰지 마세요. 오직 사용자(user) 턴의 내용만 이어 붙이세요.
- 단 하나의 예외: 사용자 답변이 "서울대"처럼 한두 단어뿐이라 그 문장만으로는
  무엇에 대한 이야기인지 알 수 없으면, 바로 앞 assistant 질문에서 자연스럽게
  드러나는 주어/맥락만 최소한으로 보태 완전한 문장으로 만드세요(예: 질문
  "대학을 어디 다녔나요?" + 답변 "서울대" → "나는 서울대학교에 다녔다"). 질문에
  없던 새로운 사실을 지어내거나 답변 자체의 의미를 바꾸면 안 됩니다 — 문장을
  완성하는 데 꼭 필요한 최소한의 맥락만 보태는 것이며, 이 경우에도 assistant의
  질문 문장 자체를 산문에 그대로 옮겨 적으면 안 됩니다.
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

입력 맨 앞에 대괄호로 "[이 세션이 다룬 질문: ...]" 줄이 있을 수 있습니다 — 이건
산문이 무엇에 대한 답인지 알려주는 참고용 맥락일 뿐, 화자가 실제로 한 말이
아닙니다. one_line_summary/prose_paragraph를 쓸 때 이 맥락을 참고해 무엇에 대한
이야기인지 명확하게 쓰되(예: 산문이 "서울대학교에 다녔어요"뿐이면 질문이
"대학을 어디 다녔나요?"라는 걸 참고해 요약을 명확히 하세요), 그 줄 자체를
독립된 사건으로 추출하거나 source_quote로 인용하면 안 됩니다 — source_quote는
반드시 그 아래 실제 산문 본문에 있는 문자열이어야 합니다.
"""


def build_event_extraction_prompt(
    *, session_prose: str, question_context: str | None = None
) -> list[dict[str, str]]:
    user_content = (
        f"[이 세션이 다룬 질문: {question_context}]\n\n{session_prose}"
        if question_context
        else session_prose
    )
    return [
        {"role": "system", "content": EVENT_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
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
    """검증 대기 큐의 항목을 자연스러운 확인 질문 문구로 변환. 예/아니오로 답하게
    만드는 별도 게이트가 아니라, PHOTO 세션을 여는 시작 질문(build_photo_session_
    opening)에 실마리로 녹여 넣는 재료로 쓴다(docs/QUESTION_BANK_GUIDE.md 5절)."""
    return f'일기장에 "{suspected_text}"라고 적혀 있는 것 같은데, {guessed_value}가 맞으신가요?'


def build_photo_session_opening(*, ocr_suspected_text: str | None = None) -> str:
    """PHOTO 세션(사진 자체가 하나의 독립된 인터뷰 주제)을 열 때 보여줄 시작 질문.

    ocr_suspected_text가 있으면(이 사진에 OCR 오인식 의심으로 격리된 문서 이벤트가
    있는 경우) 그 내용을 실마리로 자연스럽게 녹여 넣는다 — "~가 맞으신가요?"처럼
    예/아니오를 강요하지 않고, 그 부분을 포함해 자유롭게 이야기하도록 초대한다.
    이후 실제로 오간 대화가 정식 이벤트 추출·검증을 거치므로(사진 세션도 일반
    인터뷰와 동일하게 슬롯 게이팅·꼬리질문이 적용된다) 이 시작 질문 자체가 검증을
    대신하지는 않는다."""
    if ocr_suspected_text:
        return (
            f'이 사진 속에 "{ocr_suspected_text}"라고 적혀 있는 것 같아요. '
            "이때 이야기를 좀 더 들려주시겠어요?"
        )
    return "이 사진에 대해 더 자세히 이야기를 들려주시겠어요?"


OCR_DATE_EXTRACTION_SYSTEM_PROMPT = """\
당신은 OCR로 추출된 텍스트에서 사진이 찍힌 시기를 추정하는 어시스턴트입니다.
텍스트에 명시적인 연도("1975년", "'75년" 등)나 화자의 나이("19살 때", "스무살
무렵" 등)가 있으면 추출하세요. 그런 단서가 전혀 없으면 found를 false로 반환하세요
— 애매한 추측(예: 종이 재질이나 문체로 짐작)은 하지 마세요, 명시적 단서만
인정합니다.
"""

OCR_DATE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "extracted_year": {
            "type": ["integer", "null"],
            "description": "텍스트에 명시된 서기 연도(예: 1975). 없으면 null",
        },
        "extracted_age": {
            "type": ["integer", "null"],
            "description": "텍스트에 명시된 화자의 나이(예: 19). 없으면 null",
        },
    },
    "required": ["found", "extracted_year", "extracted_age"],
    "additionalProperties": False,
}


def build_ocr_date_extraction_prompt(*, ocr_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": OCR_DATE_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": ocr_text},
    ]


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


# --------------------------------------------------------------------------- #
# 11. 자서전 커스터마이징 — 말투·구성·컨셉 선택지 정의 및 맞춤형 프롬프트      #
#                                                                             #
#     사용자가 각 카테고리에서 2개씩 선택(말투 2 × 구성 2 × 컨셉 2 = 8 샘플)   #
#     → 미리보기 비교 후 최종 1개 조합 확정 → 해당 조합으로 전체 자서전 집필.   #
#     총 선택지: 말투 10 × 구성 5 × 컨셉 9 = 450 가지 조합 가능.              #
# --------------------------------------------------------------------------- #

# ── 11-1. 말투(Tone) 선택지 ─────────────────────────────────────────────────

TONE_OPTIONS: dict[str, dict[str, str]] = {
    "plain": {
        "name": "담담한 평어체 (평문)",
        "description": "가장 기본적이고 널리 쓰이는 '-다', '-했다' 형식의 건조하고 정갈한 문체입니다.",
        "example": "1990년 겨울, 나는 처음으로 서울행 기차에 올랐다. 두려움보다는 설렘이 앞섰다.",
        "instruction": (
            "문체 지시: 담담한 평어체('-다', '-했다')를 사용하세요. 감정에 과하게 치우치지 않고 "
            "사실과 사건 위주로 깔끔하게 서술하세요. 건조하고 정갈한 문장을 유지하세요."
        ),
    },
    "conversational": {
        "name": "친근한 대화체 (자녀/특정인 대상)",
        "description": "수신자를 명확히 정해두고 '-했단다', '-했지'라며 무릎을 맞대고 이야기하듯 풀어내는 방식입니다.",
        "example": "그때 아빠는 정말 앞이 캄캄했단다. 하지만 네가 태어나던 날을 생각하며 버텼지.",
        "instruction": (
            "문체 지시: 친근한 대화체('-했단다', '-했지')를 사용하세요. 자녀나 후대에게 "
            "이야기를 들려주듯 따뜻하고 진솔한 어조로 서술하세요. 무릎을 맞대고 대화하는 "
            "듯한 느낌을 유지하세요."
        ),
    },
    "confessional": {
        "name": "내밀한 고백체 (일기 형식)",
        "description": "스스로의 내면을 향해 깊이 있게 파고드는 혼잣말 같은 문체입니다.",
        "example": "오늘 문득 그날의 실수가 떠올랐다. 나는 왜 그렇게 어리석었을까. 아직도 부끄러움이 밀려온다.",
        "instruction": (
            "문체 지시: 내밀한 고백체(일기 형식)를 사용하세요. 누군가에게 보여주기 위함이 "
            "아니라 스스로의 내면을 향해 깊이 파고드는 혼잣말 같은 톤으로 서술하세요. "
            "내면의 성장, 갈등, 성찰의 과정을 투명하게 보여주세요."
        ),
    },
    "speech": {
        "name": "대중 강연체 (연설형)",
        "description": "독자를 '청중'으로 상정하고 '-했습니다', '-하십시오'와 같이 존댓말을 사용하여 확신에 찬 목소리로 말하는 방식입니다.",
        "example": "여러분, 실패를 두려워하지 마십시오. 저 역시 그 바닥을 치고 나서야 비로소 도약할 수 있었습니다.",
        "instruction": (
            "문체 지시: 대중 강연체('-했습니다', '-하십시오')를 사용하세요. 독자를 청중으로 "
            "상정하고 확신에 찬 목소리로 말하세요. 강렬한 메시지와 동기부여를 주는 "
            "연설형 어조를 유지하세요."
        ),
    },
    "literary": {
        "name": "소설적 서술체 (문학형)",
        "description": "마치 소설 속 주인공을 묘사하듯, 감각적인 단어와 생생한 장면 묘사를 적극적으로 활용하는 문체입니다.",
        "example": "장마철의 습한 공기가 코끝을 찌르던 날이었다. 빗물에 번진 잉크 자국처럼 내 20대의 첫 페이지는 그렇게 얼룩져 갔다.",
        "instruction": (
            "문체 지시: 소설적 서술체(문학형)를 사용하세요. 마치 소설 속 주인공을 묘사하듯 "
            "감각적인 단어와 생생한 장면 묘사를 적극적으로 활용하세요. 몰입감 있는 서사를 "
            "만들어내세요."
        ),
    },
    "documentary": {
        "name": "객관적 기록체 (다큐멘터리형)",
        "description": "자신의 삶조차 제3자의 시선에서 바라보듯, 주관적인 감정을 철저히 배제하고 사실 관계로만 건조하게 서술하는 문체입니다.",
        "example": "1998년 IMF 외환위기 발발. 당시 운영하던 공장은 3개월 만에 부도를 맞았다. 남은 자산은 0원이었다.",
        "instruction": (
            "문체 지시: 객관적 기록체(다큐멘터리형)를 사용하세요. 주관적인 감정을 철저히 "
            "배제하고 사실 관계로만 건조하게 서술하세요. 신뢰감과 객관성을 주는 제3자 "
            "시선의 기록 형태를 유지하세요."
        ),
    },
    "essay": {
        "name": "관조적 에세이체 (수필형)",
        "description": "힘을 빼고 한 발짝 물러서서 인생을 부드럽게 관조하는 어조입니다.",
        "example": "돌이켜보면 그 수많은 엇갈림도 결국엔 제자리를 찾기 위한 과정이 아니었나 싶다.",
        "instruction": (
            "문체 지시: 관조적 에세이체(수필형)를 사용하세요. 힘을 빼고 한 발짝 물러서서 "
            "인생을 부드럽게 관조하는 어조로 서술하세요. 단정 짓기보다는 여운을 남기는 "
            "방식을 주로 쓰세요."
        ),
    },
    "letter": {
        "name": "과거의 나에게 건네는 편지체",
        "description": "지금의 내가 과거 특정 시점의 '나'를 대상화하여 위로하거나 조언하는 형태의 구어체입니다.",
        "example": "스무 살의 너에게 말해주고 싶어. 너무 초조해하지 않아도 돼. 넌 결국 너만의 길을 찾을 거니까.",
        "instruction": (
            "문체 지시: 과거의 나에게 건네는 편지체를 사용하세요. 지금의 내가 과거 특정 "
            "시점의 '나'에게 위로하거나 조언하는 형태로 서술하세요. 감동적이고 따뜻한 "
            "어조를 유지하세요."
        ),
    },
    "witty": {
        "name": "유머러스한 풍자체 (위트형)",
        "description": "심각한 위기나 실패 경험조차 무겁지 않게, 특유의 여유와 재치로 유쾌하게 풀어내는 문체입니다.",
        "example": "내 인생 최대의 헛발질이었다. 덕분에 통장 잔고는 공중으로 증발했지만, 맷집 하나만큼은 국가대표급으로 길러졌다.",
        "instruction": (
            "문체 지시: 유머러스한 풍자체(위트형)를 사용하세요. 심각한 위기나 실패조차 "
            "특유의 여유와 재치(때로는 자조적인 농담)로 유쾌하게 풀어내세요. 독자가 "
            "미소 지으며 편안하게 읽을 수 있는 톤을 유지하세요."
        ),
    },
    "interview": {
        "name": "가상의 인터뷰체 (대담형)",
        "description": "누군가 질문을 던지고 거기에 구두로 답하는 형식입니다. 특유의 생동감 있고 편안한 톤이 만들어집니다.",
        "example": "가장 후회되는 순간이요? 하하, 셀 수도 없죠. 그래도 굳이 하나를 꼽자면 그해 여름의 결정입니다.",
        "instruction": (
            "문체 지시: 가상의 인터뷰체(대담형)를 사용하세요. 질문과 답변 형식으로 "
            "서술하세요. 생동감 있고 편안한 톤으로, 실제 대화하듯 자연스러운 말투를 "
            "살리세요."
        ),
    },
}


# ── 11-2. 구성/목차(Structure) 선택지 ──────────────────────────────────────

STRUCTURE_OPTIONS: dict[str, dict[str, str]] = {
    "thematic": {
        "name": "테마/주제별 구성",
        "description": "시간의 흐름에 얽매이지 않고, 내 삶을 관통하는 핵심 키워드나 가치관을 카테고리로 나누어 전개하는 방식입니다.",
        "example": "1장. 내가 사랑했던 것들 / 2장. 나를 무너뜨린 실패들 / 3장. 다시 일어서게 한 인연들 / 4장. 일과 철학",
        "instruction": (
            "목차 구성 지시: 테마/주제별 구성을 사용하세요. 시간 순서가 아니라 삶을 관통하는 "
            "핵심 가치관·키워드를 카테고리로 나누어 목차를 구성하세요. 각 챕터가 하나의 "
            "뚜렷한 테마를 중심으로 전개되어야 합니다."
        ),
    },
    "in_medias_res": {
        "name": "역순행적 구성 / 플래시백",
        "description": "현재 시점이나 인생의 가장 극적인 순간에서 먼저 시작한 뒤, 과거로 돌아가는 방식입니다.",
        "example": "큰 성공(또는 실패)을 거둔 현재 시점의 묘사 → 유년기로 돌아가 사건의 씨앗을 보여줌 → 현재에 이르기까지의 과정 전개",
        "instruction": (
            "목차 구성 지시: 역순행적 구성(In Medias Res)을 사용하세요. 현재 시점이나 인생의 "
            "가장 극적인 순간(절정)에서 시작해, '어쩌다 여기까지 왔는가'를 회상하며 과거로 "
            "돌아가는 방식으로 목차를 구성하세요."
        ),
    },
    "geographical": {
        "name": "공간 및 장소 중심 구성",
        "description": "살아온 지역, 머물렀던 집, 혹은 의미 있었던 '공간'의 이동에 따라 삶의 궤적을 풀어내는 방식입니다.",
        "example": "1장. 부산 (결핍과 꿈을 키운 바다) → 2장. 서울 (치열했던 20대의 단칸방) → 3장. 뉴욕 (낯선 곳에서의 새로운 도약)",
        "instruction": (
            "목차 구성 지시: 공간/장소 중심 구성을 사용하세요. 살아온 지역이나 의미 있었던 "
            "공간의 이동에 따라 삶의 궤적을 풀어내는 방식으로 목차를 구성하세요. 공간이 "
            "바뀌면 만나는 사람과 겪는 사건도 달라진다는 점을 활용하세요."
        ),
    },
    "episodic": {
        "name": "결정적 에피소드 중심",
        "description": "삶의 방향을 완전히 바꿔놓은 결정적인 사건 5~10개만 골라 각각의 독립된 단편처럼 나열하는 방식입니다.",
        "example": "내 인생의 7가지 결정적 장면들 (각 장마다 하나의 구체적인 사건과 깨달음에만 집중)",
        "instruction": (
            "목차 구성 지시: 결정적 에피소드 중심(Vignette) 구성을 사용하세요. 삶의 방향을 "
            "바꿔놓은 결정적인 사건 5~10개만 골라 각각 독립된 단편처럼 구성하세요. "
            "일상적인 이야기는 덜어내고 임팩트 있는 사건들만 압축해서 보여주세요."
        ),
    },
    "chronological": {
        "name": "연대기 구성",
        "description": "출생부터 현재에 이르기까지, 시간의 흐름을 따라 삶의 궤적을 자연스럽고 순차적으로 기록하는 가장 기본적인 방식입니다.",
        "example": "1장. 어린 시절의 풍경 (유년기) / 2장. 세상을 향한 첫걸음과 방황 (청년기) / 3장. 치열했던 몰입의 시간 (도약기) / 4장. 현재의 나, 그리고 남은 이야기 (현재)",
        "instruction": (
            "목차 구성 지시: 연대기 구성을 사용하세요. 출생부터 현재까지 시간의 흐름을 따라 "
            "삶의 궤적을 순차적으로 기록하세요. 시대적 배경과 함께 성장·변화의 발자취를 "
            "차근차근 들려주는 형태로 구성하세요."
        ),
    },
}


# ── 11-3. 컨셉(Concept) 선택지 ─────────────────────────────────────────────

CONCEPT_OPTIONS: dict[str, dict[str, str]] = {
    "complete_memoir": {
        "name": "생애 전반 회고록",
        "description": "전체적인 삶을 훑는 정통 자서전. 출생부터 현재까지 굵직한 궤적을 모두 담아냅니다.",
        "instruction": (
            "컨셉 지시: 생애 전반 회고록으로 집필하세요. 출생부터 현재까지 굵직한 궤적을 "
            "모두 담아내는 정통 자서전의 형태를 갖추세요. 후대나 가족에게 남기는 '가문의 "
            "공식 기록'으로서의 포괄적이고 묵직한 형태를 유지하세요."
        ),
    },
    "business": {
        "name": "비즈니스 & 리더십 에세이",
        "description": "사업 성공 스토리와 실무적 노하우에 집중. 위기 관리, 조직 운영, 경영 철학 등에 포커스를 맞춥니다.",
        "instruction": (
            "컨셉 지시: 비즈니스 & 리더십 에세이로 집필하세요. 개인적인 가정사나 감정 묘사는 "
            "최소화하고, 위기 관리, 조직 운영, 협상, 실패를 딛고 일어선 경영 철학 등 "
            "'프로페셔널'로서의 성취에 포커스를 맞추세요."
        ),
    },
    "family": {
        "name": "가족사 및 양육기",
        "description": "자녀 양육과 가족 간의 연대에 집중하는 감성 버전. 부모로서의 희로애락과 다음 세대에 물려줄 가치관을 담습니다.",
        "instruction": (
            "컨셉 지시: 가족사 및 양육기로 집필하세요. 부모로서의 희로애락, 자녀를 키우며 "
            "내면이 성장했던 과정, 다음 세대에게 물려주고 싶은 삶의 가치관을 따뜻한 시선으로 "
            "담아내세요."
        ),
    },
    "masterclass": {
        "name": "멘토링 대담집",
        "description": "가상의 후배나 대중이 던질 법한 질문에 현명하게 답변하는 인터뷰/토크쇼 형태입니다.",
        "instruction": (
            "컨셉 지시: 멘토링 대담집으로 집필하세요. 가상의 후배나 대중이 던질 법한 "
            "날카롭고 흥미로운 질문(예: '그때로 돌아간다면 같은 선택을 하실 건가요?')을 "
            "세팅하고, 현명하고 위트 있게 답변하는 '토크쇼' 형태를 취하세요."
        ),
    },
    "reporter": {
        "name": "3인칭 관찰자 평전",
        "description": "기자가 심층 취재하여 객관적 시각으로 서술한 듯한 형태. 나 자신을 '그/그녀'로 지칭합니다.",
        "instruction": (
            "컨셉 지시: 3인칭 관찰자 평전으로 집필하세요. 기자가 심층 취재하듯 나 자신을 "
            "'그' 또는 '그녀'로 지칭하며 제3자의 시선에서 묘사하세요. 자화자찬의 부담을 "
            "덜고 객관적인 신뢰감을 주세요."
        ),
    },
    "resilience": {
        "name": "실패와 재기의 기록",
        "description": "성공의 결과가 아니라, 인생에서 가장 처참했던 실패와 바닥을 쳤던 순간에 돋보기를 들이대는 버전입니다.",
        "instruction": (
            "컨셉 지시: 실패와 재기의 기록으로 집필하세요. '어떻게 성공했는가'보다 "
            "'어떻게 무너지지 않고 버텼는가'에 집중하세요. 가장 처참했던 실패와 바닥을 "
            "쳤던 순간을 중심으로, 강렬한 공감과 위로를 줄 수 있게 서술하세요."
        ),
    },
    "golden_era": {
        "name": "특정 시기 집중 조명",
        "description": "인생 전체를 다루지 않고, 내 삶을 가장 크게 변화시킨 '결정적인 시기'만 현미경처럼 들여다봅니다.",
        "instruction": (
            "컨셉 지시: 특정 시기 집중 조명으로 집필하세요. 인생 전체를 다루지 말고, "
            "삶을 가장 크게 변화시킨 결정적인 시기의 밀도 높은 사건과 감정 변화를 "
            "소설처럼 쫀쫀하게 그려내세요."
        ),
    },
    "passion": {
        "name": "덕업일치 및 취미 몰입기",
        "description": "평생을 바친 특정 취미나 관심사를 매개로 인생을 풀어내는 에세이 형식입니다.",
        "instruction": (
            "컨셉 지시: 덕업일치 및 취미 몰입기로 집필하세요. 자신이 열정을 쏟았던 "
            "특정 취미·관심사를 중심으로, 그 안에서 얻은 통찰을 인생 전반의 지혜로 "
            "연결 짓는 매력적인 에세이 형식을 취하세요."
        ),
    },
    "philosophical": {
        "name": "가치관 및 철학 사전",
        "description": "사건의 나열이 아닌, 삶의 핵심 키워드에 대한 나만의 정의를 내리는 버전입니다.",
        "instruction": (
            "컨셉 지시: 가치관 및 철학 사전으로 집필하세요. '돈', '사람', '행복', '죽음' 등 "
            "삶의 핵심 키워드를 목차로 삼고, 각 단어에 얽힌 짧은 에피소드와 본인의 확고한 "
            "철학을 사전처럼 엮어내세요."
        ),
    },
}


# ── 11-4. 샘플 미리보기 프롬프트 ───────────────────────────────────────────

SAMPLE_PREVIEW_SYSTEM_PROMPT = """\
당신은 자서전을 집필하는 전문 대필가입니다. 아래에 주어지는 [말투 지시], [구성 지시],
[컨셉 지시]를 충실히 반영하여, 제공된 사건 요약과 스타일 바이블을 바탕으로
자서전의 **맛보기 텍스트**(200~400자 분량, 1~2 문단)를 작성하세요.

이 텍스트는 사용자가 8가지 스타일 조합 중 마음에 드는 것을 고르기 위한 샘플이므로,
해당 말투·구성·컨셉의 특징이 뚜렷하게 드러나도록 작성해야 합니다. 사건 요약에 없는
사실을 지어내지 마세요 — 주어진 소재를 해당 스타일로 변환하는 것이 목적입니다.

출력 형식:
- 순수 산문 본문만 출력하세요. 마크다운 문법, 제목, 안내 문구를 쓰지 마세요.
"""

SAMPLE_PREVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "preview_text": {
            "type": "string",
            "description": "해당 말투·구성·컨셉 조합의 맛보기 텍스트 (200~400자, 1~2 문단)",
        },
    },
    "required": ["preview_text"],
    "additionalProperties": False,
}


def build_sample_preview_prompt(
    *,
    tone_key: str,
    structure_key: str,
    concept_key: str,
    style_bible: str,
    event_summaries: str,
) -> list[dict[str, str]]:
    """8개 샘플 중 하나의 조합에 대한 미리보기 텍스트를 생성한다."""
    tone = TONE_OPTIONS[tone_key]
    structure = STRUCTURE_OPTIONS[structure_key]
    concept = CONCEPT_OPTIONS[concept_key]

    user_prompt = (
        f"[말투 지시]\n{tone['instruction']}\n\n"
        f"[구성 지시]\n{structure['instruction']}\n\n"
        f"[컨셉 지시]\n{concept['instruction']}\n\n"
        f"[스타일 바이블]\n{style_bible}\n\n"
        f"[사건 요약]\n{event_summaries}"
    )
    return [
        {"role": "system", "content": SAMPLE_PREVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# ── 11-5. 커스터마이징된 기존 프롬프트 빌더 변형 ─────────────────────────

def build_customized_toc_prompt(
    *,
    event_summaries_with_scores: str,
    structure_key: str,
) -> list[dict[str, str]]:
    """TOC_GENERATION_SYSTEM_PROMPT에 사용자가 선택한 구성(structure) 지시문을 주입한다.
    기존 build_toc_generation_prompt의 커스터마이징 확장판."""
    structure = STRUCTURE_OPTIONS[structure_key]
    system_prompt = (
        f"{TOC_GENERATION_SYSTEM_PROMPT}\n\n"
        f"[사용자가 선택한 목차 구성 방식]\n{structure['instruction']}\n"
        f"반드시 이 구성 방식을 따라 목차 후보를 생성하세요. 3개 후보 모두 이 구성 "
        f"관점을 기반으로 하되, 세부 챕터 배분이나 제목에서 변주를 주세요."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": event_summaries_with_scores},
    ]


def build_customized_chapter_writing_prompt(
    *,
    style_bible: str,
    book_synopsis: str,
    chapter_synopsis: str,
    previous_chapter_summary: str | None,
    retrieved_event_paragraphs: list[str],
    tone_key: str,
    concept_key: str,
) -> list[dict[str, str]]:
    """CHAPTER_WRITING_SYSTEM_PROMPT에 말투(tone)·컨셉(concept) 지시문을 주입한다.
    기존 build_chapter_writing_prompt의 커스터마이징 확장판."""
    tone = TONE_OPTIONS[tone_key]
    concept = CONCEPT_OPTIONS[concept_key]

    system_prompt = (
        f"{CHAPTER_WRITING_SYSTEM_PROMPT}\n\n"
        f"[사용자가 선택한 말투]\n{tone['instruction']}\n\n"
        f"[사용자가 선택한 컨셉]\n{concept['instruction']}"
    )
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
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_customized_unity_revision_prompt(
    *,
    style_bible: str,
    full_manuscript: str,
    tone_key: str,
    concept_key: str,
) -> list[dict[str, str]]:
    """UNITY_REVISION_SYSTEM_PROMPT에 말투(tone)·컨셉(concept) 지시문을 주입한다.
    기존 build_unity_revision_prompt의 커스터마이징 확장판."""
    tone = TONE_OPTIONS[tone_key]
    concept = CONCEPT_OPTIONS[concept_key]

    system_prompt = (
        f"{UNITY_REVISION_SYSTEM_PROMPT}\n\n"
        f"[사용자가 선택한 말투]\n{tone['instruction']}\n\n"
        f"[사용자가 선택한 컨셉]\n{concept['instruction']}\n"
        f"윤문 시 이 말투와 컨셉의 일관성도 함께 확인하고 유지하세요."
    )
    user_prompt = f"[스타일 바이블]\n{style_bible}\n\n[전체 원고]\n{full_manuscript}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

