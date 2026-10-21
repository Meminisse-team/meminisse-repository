"""Characters domain operations."""
from __future__ import annotations
from app.agents.prompts.extraction import _NULLABLE_STRING



from typing import Any


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


THIRD_PARTY_RISK_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
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
            },
        }
    },
    "required": ["people"],
    "additionalProperties": False,
}


def build_third_party_risk_batch_prompt(
    *, person_names: list[str], chapter_content: str
) -> list[dict[str, str]]:
    system_prompt = (
        THIRD_PARTY_RISK_SYSTEM_PROMPT
        + "\n[분류 대상 인물] 목록의 모든 인물을 각각 한 항목씩, 정확히 같은 이름"
        "(person_name)으로 분류하세요. 목록에 없는 인물을 추가하거나 목록의 인물을 "
        "빠뜨리지 마세요."
    )
    names_block = "\n".join(f"- {name}" for name in person_names)
    user_prompt = f"[분류 대상 인물]\n{names_block}\n\n[챕터 본문]\n{chapter_content}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


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


LIFE_MILESTONE_KEYWORDS: dict[str, list[str]] = {
    "marriage": ["결혼", "혼인", "장가", "시집"],
    "childbirth": ["출산", "태어났", "낳았", "임신"],
    "career_change": ["이직", "취직", "퇴사", "창업", "입사"],
    "illness": ["투병", "수술", "입원", "발병", "진단받"],
    "bereavement": ["돌아가", "장례", "사별", "부고"],
    "relocation": ["이사", "이주", "이민"],
    "retirement": ["은퇴", "정년"],
}
