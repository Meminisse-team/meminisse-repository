"""Verification domain operations."""
from __future__ import annotations



from typing import Any


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


GROUNDEDNESS_JUDGE_SYSTEM_PROMPT = """\
아래 [챕터 본문]이 [근거 사건 목록]에 실제로 근거하는지 판정하세요. 판정
기준은 딱 하나입니다 — "독자가 이 문장을 실제로 일어난 일에 대한 검증
가능한 사실 주장으로 받아들일까?" 그렇다면, 그리고 그 사실이 근거 사건
목록 어디에도 없다면 flag하세요. 문학적 표현이 사건 목록의 문장과 토씨
하나까지 같아야 한다는 뜻이 아닙니다 — 같은 사건을 풀어 쓴 것이면 표현이
아무리 구체적이고 감각적이어도 근거 있는 것입니다.

판정은 오직 [근거 사건 목록]과의 대조로만 하세요. 화자가 실존 인물이거나
유명인과 이름이 같더라도, 당신이 사전 학습이나 검색으로 알고 있는 그 인물에
대한 실제 정보를 근거로 판정에 쓰지 마세요 — 어떤 문장이 실세계에서 사실일
가능성이 높아 보인다는 이유로 통과시키면 안 됩니다. 이 판정의 목적은 "실제로
있었던 일인가"가 아니라 "제공된 근거 사건 목록으로 뒷받침되는가"이므로, 근거
목록에 없으면 당신이 알기로 사실이더라도 반드시 flag하세요.

플래그하지 말아야 할 것 (반드시 통과):
- 이미 일어난 것으로 확인된 장면을 감각적으로 채색한 묘사. 예: 근거에
  "병원에서 시한부 선고를 받았다"만 있어도, "차트 위 검은 글씨", "복도에
  스민 소독제 냄새", "낙엽이 초침처럼 떨어졌다" 같은 분위기·비유 묘사는
  전부 정교화입니다. 새로운 사건이 아니라 있는 사건의 배경 채색입니다.
- 내적 독백·감정·다짐. 예: "시간이 천천히 흐르는 듯한 착각이 들었다",
  "이 순간을 덤으로 살아보자고 되뇌었다" — 감정 반응이지 사실 주장이
  아닙니다.
- 이미 확인된 사건(예: 배우자와 대화했다) 속 대화의 분위기·어조 묘사.
  "그녀가 대답하는 목소리는 부드러웠다"처럼 어조를 그리는 것은 정교화입니다.
  단, 이 예외는 따옴표 밖의 서술에만 적용됩니다 — 아래 직접인용 기준 참조.

직접인용은 더 엄격하게: 따옴표("")로 감싼 대사는 "실제로 이렇게 말했다"는
검증 가능한 사실 주장입니다. 근거 사건 목록에 해당 발화 내용(또는 그 취지)이
실제로 담겨 있지 않다면, 대화 장면 자체가 근거에 있더라도 그 따옴표 대사는
플래그하세요. 애매하면 플래그하는 쪽입니다 — 일반 서술의 "애매하면 통과"
기준이 직접인용에는 적용되지 않습니다.

주의 — 위 예외들은 문장의 "내용"이 감정·분위기일 때만 적용됩니다. "~을
떠올린다", "~라는 기억이 있다"처럼 회상·감상의 문형을 취했다고 자동으로
예외가 되는 것은 아닙니다. 그 문형 안에 담긴 내용이 언제·어디서·누구와
무엇이 있었다는 구체적 사실이라면(예: "도서관에서 태어났던 순간을
떠올린다" — 이건 감정 표현이 아니라 "출생 장소는 도서관"이라는 사실
주장), 회상체로 쓰였어도 아래 "반드시 플래그해야 할 것" 기준을 그대로
적용해 판정하세요.

반드시 플래그해야 할 것:
- 근거 목록에 전혀 없는 새 인물의 등장(이름, 관계 등 — 예: 근거 어디에도
  없는 "로버트"라는 사람이 갑자기 특정 행동을 하는 경우)
- 근거 목록에 없는 새로운 사건·행동 자체(감정 채색이 아니라 "무엇을
  했다/무슨 일이 있었다"는 새 사실). 예: 근거에 없는 전화 통화, 만남,
  결정, 발언 내용 자체
- 이미 알려진 사실과 모순되거나 이를 바꾸는 날짜·장소·결과의 창작

판정할 때 "이 구체적 디테일이 근거 사건과 글자 그대로 일치하는가"가
아니라 "이 문장이 근거 사건에 없던 새로운 사건/인물/결과를 주장하는가"를
물으세요. 애매할 때의 기준은 문장의 유형에 따라 다릅니다(비대칭 기준):
- 분위기·감정·감각 채색인지 아닌지 애매하면 → 통과시키세요(문학적 윤색을
  막는 것이 목적이 아닙니다).
- 그러나 새 인물의 등장, 새 사건·행동의 발생, 날짜·장소·결과의 주장이
  근거에 있는지 없는지 애매하면 → 플래그하세요. 이 세 유형은 틀렸을 때
  독자를 실제로 속이는 사실 주장이므로, 확신이 없으면 플래그하는 쪽이
  안전합니다(플래그된 문장은 삭제가 아니라 근거 기반 수정 절차로 넘어가므로,
  오탐의 비용은 낮고 미탐의 비용은 높습니다).
"""


GROUNDEDNESS_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {
                        "type": "string",
                        "description": "근거 없다고 판단된 문장(챕터 본문에서 그대로 인용)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "어떤 새로운 사실/사건이 근거 없이 추가됐는지",
                    },
                },
                "required": ["sentence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["flags"],
    "additionalProperties": False,
}


def build_groundedness_judge_prompt(
    *,
    chapter_content: str,
    source_events_text: str,
    attention_paragraphs: list[str] | None = None,
) -> list[dict[str, str]]:
    user_prompt = f"[챕터 본문]\n{chapter_content}\n\n[근거 사건 목록]\n{source_events_text}"
    if attention_paragraphs:
        # 집필 단계의 근거 태그([En]) 규약을 지키지 않은 문단 — 순수 전환·감상
        # 문단일 수도 있지만(그 경우 태그 생략이 허용됨) 근거 없는 창작일 확률이
        # 상대적으로 높으므로, 판정자가 특히 집중해서 보도록 지목한다.
        flagged_block = "\n\n".join(attention_paragraphs)
        user_prompt += (
            "\n\n[집필 시 근거 태그 없이 작성된 문단 — 특히 주의해서 검토]\n"
            f"{flagged_block}"
        )
    return [
        {"role": "system", "content": GROUNDEDNESS_JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


CHAPTER_REPAIR_SYSTEM_PROMPT = """\
아래 [챕터 본문]에서 [수정 대상] 목록에 지목된 문장들만 고치세요. 각 항목에는
문제가 된 문장과 그 사유(근거 없는 인물/사건/사실)가 적혀 있습니다.

수정 방법 — 지목된 문장마다 둘 중 하나를 택하세요:
1. [근거 사건 목록]에 실제로 있는 내용으로 바꿔 쓸 수 있으면, 근거에 맞게
   고쳐 쓰세요(문체와 흐름은 주변 문장과 자연스럽게 이어지도록).
2. 근거로 대체할 내용이 없으면 그 문장을 삭제하고, 필요하면 앞뒤 문장을
   최소한으로 다듬어 흐름이 끊기지 않게 하세요.

반드시 지킬 것:
- 지목되지 않은 문장은 한 글자도 바꾸지 마세요 — 이 작업은 전면 개고가
  아니라 국소 수술입니다.
- 수정하면서 근거 사건 목록에 없는 새로운 사실·인물·사건을 추가하지 마세요.
  화자가 실존 인물이거나 유명인과 이름이 같더라도, 당신이 사전 학습이나
  검색으로 알고 있는 그 인물의 실제 정보로 빈 자리를 채우지 마세요 — 근거
  사건 목록에 없으면 이 책 안에서는 없는 일입니다.
- 완성된 챕터 본문 전체만 출력하세요. 안내 문구·수정 내역 설명·마크다운
  문법·[E1] 같은 태그를 출력에 포함하지 마세요 — 이 출력이 그대로 저장되어
  PDF에 인쇄됩니다.
"""


def build_chapter_repair_prompt(
    *,
    chapter_content: str,
    flagged_items: list[dict[str, str]],
    source_events_text: str,
) -> list[dict[str, str]]:
    """flagged_items: [{"sentence": 문제 문장, "reason": 사유}, ...] —
    팩트체크 플래그(raw_text 기반)와 근거검증 플래그(sentence 기반)를 호출부가
    같은 모양으로 정규화해 전달한다."""
    flags_block = "\n".join(
        f"- 문장: {item['sentence']}\n  사유: {item['reason']}" for item in flagged_items
    )
    user_prompt = (
        f"[챕터 본문]\n{chapter_content}\n\n"
        f"[수정 대상]\n{flags_block}\n\n"
        f"[근거 사건 목록]\n{source_events_text}"
    )
    return [
        {"role": "system", "content": CHAPTER_REPAIR_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
