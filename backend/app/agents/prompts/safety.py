"""Safety domain operations."""
from __future__ import annotations
from app.agents.prompts.interview import ALL_SLOTS
from app.agents.prompts.interview import SLOT_GATING_SYSTEM_PROMPT



from typing import Any


CRISIS_KEYWORDS: list[str] = [
    "죽고 싶", "자살", "그만 살고 싶", "살기 싫", "사라지고 싶", "극단적 선택",
]


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


TURN_GATING_SYSTEM_PROMPT = (
    "당신은 인터뷰의 매 사용자 턴마다 서로 독립적인 두 판정을 한 번의 호출로 "
    "수행하는 분류기입니다. 두 판정은 서로 영향을 주지 않습니다 — 아래 각 판정의 "
    "기준만 따르세요.\n\n"
    "[판정 1 — strong_negative_emotion]\n"
    + TIER1_DETECTION_SYSTEM_PROMPT
    + "\n[판정 2 — newly_filled_slots]\n"
    + SLOT_GATING_SYSTEM_PROMPT
)


TURN_GATING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "strong_negative_emotion": {"type": "boolean"},
        "newly_filled_slots": {
            "type": "array",
            "items": {"type": "string", "enum": list(ALL_SLOTS.keys())},
        },
    },
    "required": ["strong_negative_emotion", "newly_filled_slots"],
    "additionalProperties": False,
}


def build_turn_gating_prompt(
    *, latest_answer: str, slots_filled: dict[str, bool]
) -> list[dict[str, str]]:
    missing = [ALL_SLOTS[k] for k, v in slots_filled.items() if not v]
    user_prompt = (
        f"아직 채워지지 않은 슬롯: {', '.join(missing) if missing else '없음'}\n"
        f"방금 답변: \"{latest_answer}\"\n"
        "strong_negative_emotion(감정 판정)과, 이 답변으로 새로 채워진 슬롯 키 배열"
        "(newly_filled_slots, 예: [\"place\", \"emotion\"])을 함께 반환하세요."
    )
    return [
        {"role": "system", "content": TURN_GATING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
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


TIER2_CRISIS_RESPONSE = """\
많이 힘든 시간을 보내고 계신 것 같아 마음이 쓰입니다.
오늘 대화는 여기서 잠시 멈추고, 편하실 때 다시 이야기 나눠요.
혹시 지금 마음이 많이 무거우시다면, 아래 기관에서 도움을 받으실 수 있습니다.

- 자살예방상담전화 1393 (24시간)
- 정신건강 위기상담전화 1577-0199

메미닛세는 의료·심리상담 서비스가 아니며, 전문적인 도움이 필요하실 때는
반드시 위 기관이나 가까운 의료기관에 연락해 주세요.
"""


NON_MEDICAL_SERVICE_DISCLOSURE = """\
메미닛세는 의료기기·디지털 치료기기가 아니며, 심리치료 또는 의료 서비스를
제공하지 않습니다. 정서적 어려움을 겪고 계신다면 전문 의료·상담 기관을
이용해 주세요.
"""


def contains_crisis_keyword(text: str) -> bool:
    return any(keyword in text for keyword in CRISIS_KEYWORDS)
