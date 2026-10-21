"""Media domain operations."""
from __future__ import annotations


from collections import Counter

from typing import Any


SCENE_DESCRIPTION_SYSTEM_PROMPT = """\
당신은 사진 분석 API가 반환한 영어 태그/사물 목록을 보고, 그 사진이 어떤
장면인지 짧은 한국어 명사구 하나로 자연스럽게 요약하는 도우미입니다.

- 입력이 영어 단어 목록이더라도 **출력은 반드시 한국어**여야 합니다. 입력
  언어를 그대로 따라 영어로 답하면 안 됩니다 — 이건 번역 작업이 아니라 한국어
  사용자에게 보여줄 한국어 문구를 새로 만드는 작업입니다.
  * 틀린 예(하지 마세요): 입력 "outdoor, mountain, river, tree" →
    "mountain and river landscape with trees" (영어로 답함, 틀림)
  * 맞는 예: 입력 "outdoor, mountain, river, tree" → "산과 강이 보이는 야외
    풍경" (한국어로 답함, 맞음)
- 주어진 목록에 있는 요소만 사용하세요. 목록에 없는 사물·상황·인원수 등을
  지어내면 안 됩니다.
- "감지된 사물" 항목에 "person×2"처럼 이름 뒤에 ×숫자가 붙어 있으면, 그 개수가
  실제로 감지된 인원/개체 수입니다 — 무시하지 말고 "아이 두 명", "사람들"처럼
  결과 문구에 반영하세요. ×표시가 없으면 1개입니다.
- 장면 태그(tags) 중 더 구체적인 것(예: "boy", "girl")이 있으면, 사물 목록의
  일반적인 이름("person")보다 그쪽을 우선해서 조합하세요 — 예를 들어 사물이
  "person×2"이고 태그에 "boy"와 "girl"이 둘 다 있으면 "아이 두 명"보다
  "소년과 소녀"처럼 더 구체적으로 쓰는 게 좋습니다.
- 완전한 문장이 아니라 "~에서 ~가 보이는 사진"처럼 짧은 명사구로 답하세요.
- 태그가 너무 많거나 서로 안 어울려도, 그중 사진의 핵심을 가장 잘 보여줄 만한
  2~4개만 골라 자연스럽게 조합하세요 — 목록 전체를 나열하지 마세요.
- 사물 탐지(objects)가 있으면 그것이 장면 태그(tags)보다 더 구체적인 단서이니
  우선하세요.
- 결과물은 순수 한국어 텍스트 명사구 하나만 반환하고, 따옴표나 설명을
  덧붙이지 마세요.
"""


def build_scene_description_prompt(
    *, objects: list[str], tags: list[str]
) -> list[dict[str, str]]:
    parts = []
    if objects:
        counts = Counter(objects)
        object_desc = ", ".join(
            f"{name}×{count}" if count > 1 else name for name, count in counts.items()
        )
        parts.append(f"감지된 사물: {object_desc}")
    if tags:
        parts.append(f"장면 태그: {', '.join(tags)}")
    user_content = "\n".join(parts) if parts else "감지된 요소 없음"
    return [
        {"role": "system", "content": SCENE_DESCRIPTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


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


def build_photo_session_opening(
    *, image_caption: str | None = None, ocr_text: str | None = None
) -> str:
    """PHOTO 세션(사진 자체가 하나의 독립된 인터뷰 주제)을 열 때 보여줄 시작 질문.

    image_caption은 더 이상 Azure Vision의 자체 caption이 아니다 — objects/tags
    분석 결과(영어)를 build_scene_description_prompt로 Solar에 넘겨 한국어
    명사구로 다듬은 결과를 받아 쓴다(app/services/media_service.py, 2026-07-16
    — Azure Caption은 지역 제약·영어 전용 제약이 있어 objects/tags 조합으로
    대체했다. app/clients/azure_vision.py 모듈 docstring 참조). ocr_text(사진
    속에서 읽어낸 손글씨/인쇄 텍스트, 예: "1990년 집 앞에서 가족들과.")와 함께
    실마리로 자연스럽게 녹여 넣는다 — "~가 맞으신가요?"처럼 예/아니오를 강요하는
    별도 확인 게이트를 두지 않고, 그 내용을 포함해 자유롭게 이야기하도록
    초대한다(과거 이 방식을 대화 중간의 예/아니오 확인 질문으로 잘못 구현했다가
    롤백한 이력이 있다 — docs/QUESTION_BANK_GUIDE.md 5절 참조). 이후 실제로 오간
    대화가 정식 이벤트 추출·검증을 거치므로(사진 세션도 일반 인터뷰와 동일하게
    슬롯 게이팅·꼬리질문이 적용된다) 이 시작 질문 자체가 검증을 대신하지는
    않는다."""
    if image_caption and ocr_text:
        return (
            f'이 사진, "{ocr_text}"라고 적혀 있고 {image_caption}인 것 같아요. '
            "이때 이야기를 좀 더 자세히 들려주시겠어요?"
        )
    if image_caption:
        return f"{image_caption}인 것 같은데, 맞나요? 이 사진에 대해 더 자세한 이야기를 들려주시겠어요?"
    if ocr_text:
        return f'이 사진 속에 "{ocr_text}"라고 적혀 있는 것 같아요. 이때 이야기를 좀 더 들려주시겠어요?'
    return "이 사진에 대해 더 자세히 이야기를 들려주시겠어요?"


EPISODE_SESSION_OPENING = (
    "네, 편하게 시작해볼까요? 그동안 물어보지 못했지만 직접 들려주고 싶으셨던 "
    "이야기가 있다면 무엇이든 좋아요."
)


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
