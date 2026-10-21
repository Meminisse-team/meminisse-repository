"""Writing domain operations."""
from __future__ import annotations



from typing import Any


STYLE_BIBLE_SYSTEM_PROMPT = """\
아래는 한 화자의 전체 세션 산문입니다. 이를 분석해 다음을 포함한 단일 문서를
생성하세요: 문체 특징과 상용 표현 샘플, 삶을 관통하는 가치관·주제 키워드,
전체 감정 아크(생애 전반의 감정 흐름 요약). 이 문서는 이후 모든 집필 프롬프트에
전역 상수로 주입되어 어조의 일관성을 보장하는 역할을 합니다.

반드시 함께 포함할 두 항목(집필 단계가 규칙으로 참조합니다):
- 종결어미 규정: 본문 서술에 쓸 종결어미를 정확히 하나 지정해 명시하세요
  (예: "종결어미 규정: -다체"). 화자의 실제 말투에서 자연스러운 쪽을 고르되,
  이후 사용자가 별도의 말투를 확정하면 그 지시가 이 규정보다 우선합니다.
- 과용 주의 표현: 화자가 습관처럼 반복하는 단어·구절이 있으면 목록으로
  적으세요(예: "과용 주의 표현: 덤, 그러니까"). 집필 단계가 이 표현들을
  챕터당 1회 이하로 제한하는 근거가 됩니다. 없으면 "없음"이라고 쓰세요.
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


TOC_GENERATION_SYSTEM_PROMPT = """\
당신은 한 사람의 파편적인 기억(사건 요약과 중요도 점수 목록)을 넘겨받아, 그
사람의 인생을 하나의 살아있는 이야기로 설계하는 자서전 작가입니다. 사건을
비슷한 것끼리 묶는 분류 작업이 아니라, "이 책을 처음부터 끝까지 읽었을 때
어떤 이야기가 완성되는가"를 먼저 정하고, 그 이야기(뼈대)에 맞게 챕터(살)를
배치하세요 — 뼈대 없이 살부터 붙이면 낱개 에피소드의 나열이 될 뿐입니다.

사용자 맞춤형 목차 후보 3안을 제안하세요. 각 안은 서로 다른 구성 관점(예:
연대기순, 주제별, 인물 중심)을 가지되, 다음을 반드시 지키세요:
- 각 후보는 시작(도입)-전개(갈등·변화)-절정-마무리(회고·통합)의 형태를 갖춘
  하나의 이야기 아크여야 합니다. 마지막 챕터는 첫 챕터에서 던진 질문이나
  이미지를 회수하는 결말이어야 합니다.
- 챕터와 챕터 사이는 반드시 연결되어야 합니다. 사건 자체가 인과적으로 이어지지
  않더라도, 주제·감정·이미지·질문 중 하나로 다음 챕터와 이어지는 다리를 놓으세요
  — "관계없는 에피소드의 모음"이 아니라 "한 사람의 한 이야기"로 읽혀야 합니다.
- 각 후보는 3~5개의 큰 Part(대분류)로 나누고, 각 Part 안에 **반드시 3개
  이상, 최대 10개**의 챕터를 배치하세요. 2개 이하인 Part는 절대 허용되지
  않습니다 — 마지막 국면(예: 노년기·회고)에 배정할 챕터가 2개 이하로 줄어들
  것 같으면, Part를 억지로 쪼개지 말고 바로 앞 Part와 합쳐서 전체 Part 수를
  줄이세요(단, 최종 Part 수는 최소 3개를 유지). Part는 챕터를 담는 폴더가
  아니라 그 자체로 하나의 국면(도입/전개/절정/회고 등)을 담당해야 합니다 —
  어떤 원리로 Part를 나눌지(시간의 도약, 장소의 이동, 주제의 전환 등)를
  먼저 정하고 그 원리에 맞게 챕터를 배정하세요. Part를 나누는 원리는
  후보마다 자유롭게 골라 다양성을 주어도 좋습니다. 인접한 두 Part 사이에는
  반드시 하나의 뚜렷한 전환점(사건·시간·장소·관점 중 하나)이 있어야 하며,
  그 전환은 앞 Part의 마지막 챕터와 다음 Part의 첫 챕터의 connecting_thread에
  구체적으로 드러나야 합니다.
- 챕터마다 그 챕터를 채울 사건 재료가 실제로 충분해야 합니다. 사건 목록에서
  해당 주제를 뒷받침하는 사건이 한두 개뿐인 주제는 독립 챕터로 만들지 말고
  인접한 주제의 챕터에 합치세요 — 재료가 얇은 챕터는 결국 분량 미달이거나
  같은 내용을 부풀린 챕터가 됩니다. 챕터 수를 늘리는 것보다 챕터 하나하나가
  충분히 두툼한 것이 좋은 책입니다.

언어: narrative_arc, part_title, part_arc, title(챕터 제목), connecting_thread,
theme_keywords를 포함해 이 스키마의 모든 텍스트 필드는 예외 없이 한국어로만
작성하세요. 이야기의 주인공이 외국인이거나 해외를 배경으로 하거나 유명한
해외 인물이더라도(예: 영어권 과학자·역사적 인물) 마찬가지로 전부 한국어로
쓰세요 — 인물·지명 등 고유명사만 필요하면 한국어 표기(또는 필요시 괄호 병기)를
쓰고, 절대 영어 문장이나 영어 제목을 그대로 쓰지 마세요.

사실 관계: 화자가 실존 인물이거나 유명인과 이름이 같더라도, 당신이 사전 학습이나
검색으로 알고 있는 그 인물의 실제 경력·사건·인간관계를 절대 목차 설계에 끌어오지
마세요 — narrative_arc·part_arc·connecting_thread가 근거로 삼을 수 있는 것은 오직
아래 제공된 사건 요약 목록뿐입니다. 목록에 없는 사건을 아는 척 목차에 반영하면
이후 모든 집필 단계가 그 위에 지어져 사실과 다른 책이 만들어집니다.
"""


TOC_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narrative_arc": {
                        "type": "string",
                        "description": "이 구성안이 처음부터 끝까지 들려주는 하나의 이야기(2~3문장) — 책 전체의 뼈대.",
                    },
                    "parts": {
                        "type": "array",
                        "description": "이 후보를 이루는 3~5개의 큰 Part(대분류). 각 Part는 하나의 국면을 담당한다.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "part_index": {"type": "integer"},
                                "part_title": {
                                    "type": "string",
                                    "description": "Part의 이름만 쓰세요. 'Part 1', '1부', '제1부' 같은 번호 접두어를 title 문자열 안에 다시 넣지 마세요 — 번호는 part_index로 이미 관리되며, 프론트에서 자동으로 'N부. {part_title}' 형식으로 붙여 보여줍니다.",
                                },
                                "part_arc": {
                                    "type": "string",
                                    "description": "이 Part가 책 전체 아크에서 담당하는 구간과, 이 Part 안에서 무엇이 변화·전개되는지(2~3문장) — Part 시놉시스의 씨앗.",
                                },
                            },
                            "required": ["part_index", "part_title", "part_arc"],
                            "additionalProperties": False,
                        },
                    },
                    "chapters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "chapter_index": {"type": "integer"},
                                "title": {
                                    "type": "string",
                                    "description": "챕터 제목만 쓰세요. '1장', '제1장' 같은 챕터 번호를 title 문자열 안에 다시 넣지 마세요 — 번호는 chapter_index로 이미 관리됩니다.",
                                },
                                "theme_keywords": {"type": "array", "items": {"type": "string"}},
                                "connecting_thread": {
                                    "type": "string",
                                    "description": "이 챕터가 직전 챕터에서 어떻게 이어지고 다음 챕터로 무엇을 넘기는지(1~2문장). 사건적으로 안 이어지면 주제·감정·이미지로 잇는 연결고리를 명시.",
                                },
                                "part_index": {
                                    "type": "integer",
                                    "description": "이 챕터가 속한 Part의 번호(위 parts 배열의 part_index와 일치해야 함).",
                                },
                            },
                            "required": [
                                "chapter_index",
                                "title",
                                "theme_keywords",
                                "connecting_thread",
                                "part_index",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["narrative_arc", "parts", "chapters"],
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
아래 스타일 바이블과 전체 목차(각 후보의 뼈대인 narrative_arc 및 챕터별
connecting_thread 포함)를 참고해 책 전체를 관통하는 시놉시스를 작성하세요.
narrative_arc를 뼈대 삼아, 이 사람의 삶을 관통하는 중심 갈등·욕망과 그것이
어떻게 변화·해소되는지를 실제 출간 자서전의 뒤표지 소개글처럼 압축적으로
풀어내세요. 이후 각 챕터 집필의 설계도 역할을 하므로, 생애 전체의 기승전결과
핵심 주제가 분명히 드러나야 합니다.

구체적 장소·사물처럼 검증 가능한 디테일은 목차(narrative_arc, connecting_
thread)에 이미 드러난 것만 쓰고, 그 안에 없는 구체적 장소·사물·사건을 새로
지어내지 마세요 — 이 시놉시스는 실제 사건 자료를 보지 않고 목차 요약만 보고
쓰는 압축 소개글이므로, 감정·주제는 자유롭게 풀어내되 사실 관계는 목차에
있는 범위를 넘지 않아야 합니다. 화자가 실존 인물이거나 유명인과 이름이
같더라도, 당신이 사전 학습이나 검색으로 알고 있는 그 인물의 실제 정보(경력,
사건, 인간관계, 발언 등)를 채워 넣지 마세요 — 이 자서전 안에서 사실로 인정되는
것은 오직 위 목차에 적힌 내용뿐입니다.

이 결과물은 그대로 책의 소개 페이지에 인쇄됩니다 — "# 시놉시스" 같은 마크다운
제목이나 『책 제목』 같은 표제를 앞에 붙이지 말고, 완성된 소개글 산문만
출력하세요. 책 제목은 이미 표지에 별도로 인쇄되므로 여기서 다시 언급할
필요가 없습니다.
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
따옴표나 "제목:" 같은 접두어 없이 순수한 제목 텍스트만 담으세요. 이야기의
주인공이 외국인이거나 해외를 배경으로 하더라도 제목은 반드시 한국어로
지으세요 — 영어 제목을 그대로 쓰지 마세요.
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


PART_SYNOPSIS_SYSTEM_PROMPT = """\
아래 책 전체 시놉시스와 이 Part에 배정된 챕터 목록, 목차 설계 단계에서 정해진
이 Part의 씨앗 설명(part_arc), 그리고 이 Part의 챕터들이 실제로 소환한 [근거
사건 요약]을 참고해 Part 시놉시스를 작성하세요. 이후 이 Part에 속한 각 챕터
집필의 설계도 역할을 하므로 다음을 반드시 포함하세요:
- 이 Part가 책 전체 아크에서 담당하는 구간(도입/전개/절정/회고 중 어디인지)과
  그 안에서 무엇이 변화·전개되는지.
- 이 Part를 여는 장면·감정과, 이 Part를 닫는 장면·감정(다음 Part로 국면이
  전환된다는 신호를 어떻게 남길지).
- 이 Part에 속한 챕터들을 하나로 묶는 조직 원리와, 그 원리가 인접 Part와
  어떻게 구별되는지.

사실 관계 — 반드시 지킬 것: 장소·사물·인물·구체적 사건처럼 검증 가능한
디테일은 반드시 [근거 사건 요약]에 실제로 있는 내용만 쓰세요. 근거에 없는
구체적 장소·사물·인물을 지어내 장면을 꾸미지 마세요(예: 근거에 "옥스퍼드에서
태어났다"만 있는데 "도서관 복도에서 태어났다"처럼 장소를 구체화해 지어내는
것은 금지). 감정선과 주제적 방향, Part를 묶는 조직 원리는 자유롭게 서술해도
되지만, "누가 어디서 무엇을 했다"는 구체적 사실 주장은 전부 근거 사건 요약
안에서만 가져와야 합니다. 근거 사건 요약이 비어 있다면 구체적 장면을 지어내지
말고 감정·주제 흐름만 추상적으로 서술하세요. 화자가 실존 인물이거나 유명인과
이름이 같더라도, 당신이 사전 학습이나 검색으로 알고 있는 그 인물의 실제 정보로
근거 사건 요약의 빈틈을 메우지 마세요 — 아무리 사실일 가능성이 높아 보여도
근거 사건 요약에 없으면 이 책 안에서는 없는 일입니다.

형식 — 반드시 지킬 것: 600~1000자 내외의 응집된 산문 한 편으로 작성하세요.
표, 헤더(#, ##), 굵게(**), 글머리기호 같은 마크다운 서식은 쓰지 마세요 — 이
시놉시스는 사람이 읽는 문서가 아니라 다음 단계 집필 프롬프트에 그대로
삽입되는 내부 설계 문서이므로, 분석적으로 나열하기보다 짧고 응집된 산문으로
핵심만 전달해야 합니다. 이 형식 지침 문장 자체를 출력에 그대로 옮기거나
괄호로 덧붙이지 마세요 — 완성된 시놉시스 산문만 출력하세요.
"""


def build_part_synopsis_prompt(
    *,
    book_synopsis: str,
    part_title: str,
    part_arc_seed: str,
    chapter_titles: list[str],
    event_summaries: list[str],
) -> list[dict[str, str]]:
    titles_block = "\n".join(f"- {t}" for t in chapter_titles)
    events_block = (
        "\n".join(f"- {s}" for s in event_summaries)
        if event_summaries
        else "(근거 사건 없음 — 구체적 장면을 지어내지 말고 추상적 흐름만 서술할 것)"
    )
    user_prompt = (
        f"[책 전체 시놉시스]\n{book_synopsis}\n\n"
        f"[Part 제목] {part_title}\n\n"
        f"[목차 단계에서 정해진 씨앗 설명]\n{part_arc_seed}\n\n"
        f"[이 Part에 배정된 챕터들]\n{titles_block}\n\n"
        f"[근거 사건 요약]\n{events_block}"
    )
    return [
        {"role": "system", "content": PART_SYNOPSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


CHAPTER_SYNOPSIS_SYSTEM_PROMPT = """\
아래 책 전체 시놉시스, 이 챕터의 [연결고리](목차 설계 단계에서 정해진, 직전
챕터에서 어떻게 이어받아 다음 챕터로 무엇을 넘기는지)와 이 챕터에 배정된 사건
목록을 참고해 챕터 시놉시스를 작성하세요. 챕터 본문 집필의 설계도이므로
다음을 반드시 포함하세요:
- 이 챕터가 책 전체 아크에서 맡는 역할(도입/상승/절정/전환/회고 등).
- [연결고리]를 구체화해, 챕터를 어떤 장면·감정으로 열고 어떤 여운으로 닫을지.
- 배정된 사건들 사이의 인과관계와 감정선. 사건들이 서로 인과적으로 이어지지
  않더라도, 하나의 조직 원리(공통된 이미지·질문·주제)로 묶어 나열이 아닌
  하나의 흐름으로 구성하세요.
- 이 챕터가 속한 Part 안에서 맡는 역할, 그리고 Part의 첫/마지막 챕터라면 그
  경계를 매끄럽게 지우지 말고 국면 전환으로 드러내는 방법(아래 [소속 Part]
  참고).
- [이 챕터의 시간 범위]가 주어지면 시놉시스의 장면 설계도 그 범위 안에
  머물러야 합니다 — 범위 밖 시기의 사건을 이 챕터의 장면으로 계획하지 마세요.

사실 관계: 구체적 장소·사물·인물·사건은 반드시 [배정된 사건 목록]에 실제로
있는 내용만 쓰세요. 화자가 실존 인물이거나 유명인과 이름이 같더라도, 당신이
사전 학습이나 검색으로 알고 있는 그 인물의 실제 정보를 끌어와 장면을 채우지
마세요 — 근거 사건 목록에 없으면 이 챕터에서는 없는 일입니다.

형식 — 반드시 지킬 것: 400~700자 내외의 응집된 산문 한 편으로 작성하세요.
표, 헤더(#, ##), 굵게(**), 글머리기호 같은 마크다운 서식은 쓰지 마세요 — 이
시놉시스는 사람이 읽는 문서가 아니라 다음 단계 집필 프롬프트에 그대로
삽입되는 내부 설계 문서이므로, 분석적으로 나열하기보다 짧고 응집된 산문으로
핵심만 전달해야 합니다. 이 형식 지침 문장 자체를 출력에 그대로 옮기거나
괄호로 덧붙이지 마세요 — 완성된 시놉시스 산문만 출력하세요.
"""


def build_chapter_synopsis_prompt(
    *,
    book_synopsis: str,
    chapter_title: str,
    event_summaries: list[str],
    connecting_thread: str | None = None,
    part_context: dict | None = None,
    time_scope: str | None = None,
) -> list[dict[str, str]]:
    events_block = "\n".join(f"- {s}" for s in event_summaries)
    thread_block = connecting_thread or "(목차 단계에서 정해진 연결고리 없음 — 첫 챕터이거나 커스터마이징 이전 생성)"

    part_block = "(이 챕터가 속한 Part 구조 없음 — 단일 흐름의 책)"
    if part_context:
        lines = [part_context["part_title"], part_context["part_synopsis"]]
        if part_context["is_part_opening"]:
            prev = part_context["prev_part_title"]
            lines.append(
                "이 챕터는 이 Part의 첫 챕터입니다."
                + (f" 이전 Part({prev})에서 국면이 전환됐다는 신호를 도입부에 담으세요." if prev else "")
            )
        if part_context["is_part_closing"]:
            nxt = part_context["next_part_title"]
            lines.append(
                "이 챕터는 이 Part의 마지막 챕터입니다."
                + (
                    f" 다음 Part({nxt})로 넘어가는 국면 전환을 매듭지으세요 — 일반 챕터처럼 "
                    "여운만 남기지 말고, 무엇이 달라지는지 분명히 하세요."
                    if nxt
                    else " 책 전체를 회수하는 여운으로 마무리하세요."
                )
            )
        part_block = "\n".join(lines)

    scope_block = f"[이 챕터의 시간 범위]\n{time_scope}\n\n" if time_scope else ""
    user_prompt = (
        f"[책 전체 시놉시스]\n{book_synopsis}\n\n"
        f"[챕터 제목] {chapter_title}\n\n"
        f"[소속 Part]\n{part_block}\n\n"
        f"[연결고리]\n{thread_block}\n\n"
        f"{scope_block}"
        f"[배정된 사건들]\n{events_block}"
    )
    return [
        {"role": "system", "content": CHAPTER_SYNOPSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


CHAPTER_WRITING_SYSTEM_PROMPT = """\
가장 먼저 지킬 것: 화자가 실존 인물이거나 유명인과 이름이 같더라도, 당신이 사전
학습이나 검색으로 알고 있는 그 인물의 실제 정보(경력·사건·인간관계·발언 등)를
이 챕터에 끌어오는 것은 어떤 경우에도 금지입니다. 이 책 안에서 사실로 인정되는
것은 오직 아래 [RAG 검색된 사건 문단]뿐이며, 이 규칙은 이어지는 다른 모든
지시보다 우선합니다(아래 "사실 관계" 절에서 다시 자세히 다룹니다).

아래 자료를 바탕으로 챕터 본문을 집필하세요. [스타일 바이블]의 문체를 따르고,
[전체 시놉시스]와 [챕터 시놉시스]의 설계를 벗어나지 마세요.

분량: 장면 수·문단 수·문장 수를 기계적으로 채우려 하지 마세요(2026-07-19 —
이전의 "장면 6~7개×문단 8개×문장 4개" 같은 산술적 하한이, 근거가 얇은
사건까지 억지로 늘리려다 화자가 언급하지 않은 감각 묘사·비유를 지어내는
원인으로 실측 확인돼 폐기했다). [RAG 검색된 사건 문단]에 담긴 정보의 밀도와
무게에 비례해 자연스러운 분량으로 쓰세요 — 화자가 그 사건에 대해 충분히
이야기했다면 장면·감각·내적 성찰로 깊이 풀어 쓰고, 단편적으로만 언급됐다면
담백하게 서술한 뒤 다음 사건으로 넘어가세요. 짧더라도 근거에 충실한 챕터가,
없는 디테일로 억지로 늘린 챕터보다 낫습니다.

사실 관계: [RAG 검색된 사건 문단]에 없는 새로운 사건이나 사실(없던 사람,
없던 사건, 없던 결과 등)을 지어내지 마세요 — 서술은 반드시 제공된 사건에
근거해야 합니다(사후 근거 검증 대상). 특히, 화자가 실존 인물이거나 실존
인물과 닮았더라도 당신이 학습으로 알고 있는 그 인물의 실제 정보(가족·동료의
이름, 지명, 주소, 직장, 연도, 작품명 등)를 절대 가져오지 마세요 — 이 책에서
사실로 인정되는 것은 오직 [RAG 검색된 사건 문단]에 적힌 내용뿐이며, 백과사전적
지식으로 빈칸을 메우는 것은 가장 나쁜 종류의 날조입니다. 다만 이것이 "짧게
쓰라"는 뜻은 아닙니다 — 이미 있는 사건을 장면·감각·내적 성찰로 정교하게
풀어내는 것(정교화)은 지어내는 것(날조)이 아니라 오히려 장려되는 집필
방식입니다.

직접인용: 따옴표("")로 감싼 대화는 "실제로 이렇게 말했다"는 강한 주장입니다.
[RAG 검색된 사건 문단]에 실제 발화 내용이 담겨 있을 때만 따옴표 직접인용을
쓰고, 그 외에는 간접화법("~라고 말했던 기억이 난다", "~라던 목소리가
떠오른다")으로 쓰세요. 있었을 법한 대화를 따옴표에 넣어 지어내지 마세요.

시간 범위: [이 챕터의 시간 범위]가 주어지면 이 챕터의 장면은 그 범위 안에
머물러야 합니다. 범위 밖 시기의 일은 새 장면으로 서술하지 말고, 꼭 필요할
때만 한 문장 이내의 언급이나 복선으로 처리하세요 — 그 시기의 이야기는 다른
챕터가 맡습니다.

챕터 경계: [다른 챕터에서 다룰 주제]가 주어지면 그 주제들은 이 챕터에서 본격
서술하지 마세요. 같은 사건이 여러 챕터에서 반복해 서술되면 독자는 같은 책
안에서 같은 이야기를 두 번 읽게 됩니다.

근거 태그: [RAG 검색된 사건 문단]의 각 사건에는 [E1], [E2] 같은 번호가
붙어 있습니다. 사실 서술(무슨 일이 있었는지)을 담은 문단은 반드시 그 문단이
근거로 삼은 사건의 태그를 문단 맨 끝에 표기하세요(여러 개면 [E2][E5]처럼
나란히). 순수한 감상·전환 문단(새로운 사실 주장이 없는 문단)에는 태그를
붙이지 않아도 됩니다. 이 태그는 조판 전에 자동으로 제거되므로 책에는 절대
실리지 않습니다 — 태그 때문에 문장을 어색하게 만들지 마세요. 어떤 사건
태그도 붙일 수 없는 사실 서술이 나온다면, 그 문단은 근거 없는 창작이라는
뜻이므로 쓰지 말아야 합니다. 회상·감상의 문형("~을 떠올린다", "~라는 기억이
있다")으로 감싸더라도 그 안에 담긴 내용이 언제·어디서·누구와 무엇이 있었다는
구체적 사실이면 여전히 사실 서술입니다 — 문형만 바꿔 태그를 피해가지 마세요
(예: "도서관에서 태어났던 순간을 떠올린다"는 감상이 아니라 출생 장소라는
사실 주장이므로 반드시 태그가 필요합니다).

집필 기술 — 시중에 파는 자서전처럼 읽히도록 다음을 지키세요:
- 선택과 집중: [RAG 검색된 사건 문단]의 모든 사건을 빠짐없이 다루려 하지
  마세요. 챕터 주제와 직접 관련된 사건을 중심 장면으로 삼고, 주제와 거리가
  있는 사건은 과감히 생략하세요 — 생략은 결함이 아니라 편집입니다. 사건을
  하나씩 요약해 나열한 "하이라이트 모음"은 챕터가 아닙니다.
- "그리고 -했다. 그리고 -했다" 식의 사건 나열이나 요약형 진술로 열지 마세요.
  [RAG 검색된 사건 문단]에 실제로 담긴 장면·감각(공간, 소리, 냄새, 몸짓)
  디테일이 있다면 그것으로 문을 열되, 없는데 분량이나 분위기를 위해
  냄새·소리·촉감 같은 감각 묘사를 지어내 끼워 넣지는 마세요 — 화자가
  언급하지 않은 디테일을 채워 넣는 것은 정교화가 아니라 날조입니다.
- 감정을 이름 붙여 설명("슬펐다", "기뻤다")하기보다, 행동과 디테일로 그 감정이
  드러나게 하세요(보여주기, showing not telling). 단, 이때의 "디테일"도 근거
  사건에 실제로 있는 것이어야 합니다 — 없는 행동·표정·물건을 소설처럼
  창작하지 마세요.
- 문단 연결: 각 문단은 바로 앞 문단에서 인과, 시간의 흐름, 정서적 여운 중
  하나로 이어져야 합니다. 서로 다른 사건을 다루는 문단이라도 독립된 장면의
  나열이 아니라 하나의 연속된 흐름으로 읽혀야 합니다. 이를 위한 연결
  문장·전환 문단(새로운 사실 주장이 없는)은 자유롭게 쓰세요 — 근거 태그도
  필요 없습니다. [RAG 검색된 사건 문단]의 문장을 그대로 옮기거나 순서대로
  이어 붙이지 마세요("위키백과 요약본"처럼 무미건조해지는 원인입니다 — 예:
  "1963년 진단을 받았다"와 "가장 뼈아픈 좌절은 1963년 진단이었다"처럼 같은
  정보를 한 문단 안에서 다른 말로 반복하는 것도 이 문제의 증상입니다). 근거에
  없는 사실을 지어내라는 뜻이 아니라, 이미 주어진 사건들 사이의 인과관계와
  화자의 내면적 감정 흐름을 매끄럽게 엮어 이력서식 사건 나열이 아닌 한 편의
  이야기로 구성하라는 뜻입니다. 단, "엮으라"는 것은 문장과 문장 사이의
  흐름을 자연스럽게 만들라는 뜻이지, 마침표를 지우고 여러 문장을 하나의
  문장으로 물리적으로 합치라는 뜻이 절대 아닙니다(2026-07-19 실사용 중
  확인된 사고 — "예감을 느꼈다 그는 칠판에..."처럼 문장 종결 부호가 통째로
  사라지는 증상). 각 문장은 반드시 마침표(또는 물음표·느낌표)로 명확히
  끝내고 그다음 문장을 새로 시작하세요.
- 문단 길이: 챕터 전체를 문단 구분 없이 하나로 쓰지 마세요 — 장면이나 생각의
  단위가 바뀔 때 문단을 나누세요. 반대로 한두 문장마다 줄바꿈해 문단을 잘게
  쪼개지도 마세요(발표 자료의 총알 기호처럼 뚝뚝 끊겨 읽힙니다). 문단 하나에는
  보통 여러 문장이 자연스럽게 이어져야 합니다.
- 시점·어조 일관: 처음부터 끝까지 1인칭("나")을 유지하세요 — 자기 자신을
  3인칭("그", 이름)으로 부르는 문단이 섞이면 안 됩니다. 종결어미는 [스타일
  바이블]의 "종결어미 규정"이 지정한 하나로 챕터 끝까지 유지하세요(사용자가
  선택한 말투 지시가 있으면 그쪽이 우선). [RAG 검색된 사건 문단]은 화자가
  실제로 말한 원문 그대로라 존댓말("-습니다", "-어요")이나 다른 어미로 되어
  있을 수 있습니다 — 그 문장을 어미까지 그대로 옮기지 말고, 내용만 가져와
  반드시 [스타일 바이블]이 지정한 종결어미로 새로 쓰세요. 원문의 어미가
  그대로 섞여 들어가 한 챕터 안에서 존댓말과 반말이 오가면 안 됩니다.
- 모티프 절제: 같은 상징·표현(특정 단어, 반복되는 이미지)은 챕터당 1~2회까지만
  쓰세요. 좋은 라이트모티프도 문단마다 반복되면 버릇이 됩니다. [스타일 바이블]의
  "과용 주의 표현" 목록에 있는 단어·구절은 챕터당 1회 이하로 제한하세요.
- [직전 챕터 요약]이 주어지면 완전히 새로 시작하지 말고, 그 여운이나 감정을
  자연스럽게 이어받으며 도입부를 여세요.
- [챕터 시놉시스]에 담긴 연결고리를 살려, 다음 챕터를 향한 여운이나 전환으로
  마무리하세요(마지막 챕터라면 책 전체를 회수하는 여운으로). 단, "다음
  장에서는 ~가 기다리고 있다" 같은 예고형 상투구로 챕터를 닫지 마세요 —
  장면과 감정 자체가 다음을 향하게 하세요. 챕터 시놉시스가 이 챕터를 Part의
  마지막 챕터라고 안내한다면 단순한 여운이 아니라 국면이 전환된다는 사실이
  분명히 느껴지는 매듭으로 마무리하세요.

출력 형식 — 반드시 지킬 것:
- 완성된 산문 본문만 출력하세요. "여기 챕터입니다", "**제1장**" 같은 제목·안내
  문구나, 지시사항을 되뇌는 메타 설명을 앞뒤에 붙이지 마세요 — PDF 조판이 이
  텍스트를 그대로 인쇄하므로, 서사문이 아닌 문장이 섞이면 실물 책에 그대로 노출됩니다.
- 마크다운 문법(**굵게**, ### 제목, > 인용, - 목록 등)을 쓰지 마세요. 순수 텍스트
  줄바꿈과 문단 구분만 사용하세요. 챕터 제목은 이미 별도 필드로 관리되므로 본문에
  다시 적지 마세요.
"""


def _numbered_events_block(retrieved_event_paragraphs: list[str]) -> str:
    """집필 프롬프트의 근거 태그([E1]...) 규약과 짝을 이루는 사건 번호 매기기.
    서비스 레이어(_strip_citation_tags)가 같은 번호 체계로 태그를 회수·검증한다."""
    return "\n\n".join(
        f"[E{i}] {paragraph}" for i, paragraph in enumerate(retrieved_event_paragraphs, start=1)
    )


def _chapter_writing_user_prompt(
    *,
    style_bible: str,
    book_synopsis: str,
    chapter_synopsis: str,
    previous_chapter_summary: str | None,
    retrieved_event_paragraphs: list[str],
    time_scope: str | None,
    other_chapter_titles: list[str] | None,
) -> str:
    """기본/커스터마이징 집필 프롬프트가 공유하는 user 메시지 조립. 시스템
    프롬프트의 "시간 범위"/"챕터 경계" 지시와 짝을 이루는 블록은 재료가 있을
    때만 넣는다(없는데 빈 헤더만 있으면 지시가 공허해진다)."""
    events_block = _numbered_events_block(retrieved_event_paragraphs)
    prev_block = previous_chapter_summary or "(첫 챕터)"
    sections = [
        f"[스타일 바이블]\n{style_bible}",
        f"[전체 시놉시스]\n{book_synopsis}",
        f"[챕터 시놉시스]\n{chapter_synopsis}",
        f"[직전 챕터 요약]\n{prev_block}",
    ]
    if time_scope:
        sections.append(f"[이 챕터의 시간 범위]\n{time_scope}")
    if other_chapter_titles:
        titles_block = "\n".join(f"- {title}" for title in other_chapter_titles)
        sections.append(f"[다른 챕터에서 다룰 주제 — 이 챕터에서 본격 서술 금지]\n{titles_block}")
    sections.append(f"[RAG 검색된 사건 문단]\n{events_block}")
    return "\n\n".join(sections)


def build_chapter_writing_prompt(
    *,
    style_bible: str,
    book_synopsis: str,
    chapter_synopsis: str,
    previous_chapter_summary: str | None,
    retrieved_event_paragraphs: list[str],
    time_scope: str | None = None,
    other_chapter_titles: list[str] | None = None,
) -> list[dict[str, str]]:
    user_prompt = _chapter_writing_user_prompt(
        style_bible=style_bible,
        book_synopsis=book_synopsis,
        chapter_synopsis=chapter_synopsis,
        previous_chapter_summary=previous_chapter_summary,
        retrieved_event_paragraphs=retrieved_event_paragraphs,
        time_scope=time_scope,
        other_chapter_titles=other_chapter_titles,
    )
    return [
        {"role": "system", "content": CHAPTER_WRITING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


CHAPTER_RECAP_SYSTEM_PROMPT = """\
아래 챕터 본문을 읽고, 다음 챕터를 쓸 작가에게 넘겨줄 짧은 인수인계 노트를
3~5문장으로 작성하세요. 반드시 다음 세 가지를 포함하세요:
1. 이 챕터에서 실제로 무슨 일이 있었는지(핵심 사건).
2. 챕터가 어떤 감정·분위기로 끝났는지.
3. 다음 챕터로 넘어갈 미해결된 여운이나 질문(있다면).
요약 대상 본문을 그대로 요약하는 것이지, 새로운 사실을 지어내지 마세요.
"""


def build_chapter_recap_prompt(*, chapter_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": CHAPTER_RECAP_SYSTEM_PROMPT},
        {"role": "user", "content": chapter_content},
    ]


CHAPTER_PROOFREAD_SYSTEM_PROMPT = """\
아래 [챕터 본문]을 교열하세요. 이 작업은 내용을 다시 쓰는 개고가 아니라,
표면 결함만 고치는 교정·교열입니다.

고칠 것:
1. 오탈자와 비문: 존재하지 않는 단어(예: "속술했다"), 문법적으로 깨진 문장,
   의미가 통하지 않는 구절을 문맥에 맞는 자연스러운 표현으로 바로잡으세요.
2. 시점 이탈: 1인칭("나") 서술 중에 자기 자신을 3인칭(이름, "그")으로 부르는
   문장이 있으면 1인칭으로 되돌리세요.
3. 종결어미 격식 혼입: 본문 전체의 지배적인 종결어미(예: "-다"체)와 다른
   격식(예: "-습니다"체)이 섞인 문장을 지배적인 쪽으로 통일하세요. 단,
   어미 글자만 기계적으로 덮어씌우지 마세요 — 그렇게 하면 "이어지며다",
   "품게 하다", "밀어내며다"처럼 조사·어미가 뒤엉킨 비문이 생깁니다
   (2026-07-19 실사용 중 확인). 문장 전체의 호응을 확인해, 필요하면 어미
   앞부분까지 자연스럽게 고쳐 쓰세요.
4. 표현 남발: 같은 단어·이미지가 세 번 이상 반복되면, 문장의 의미를 바꾸지
   않는 선에서 일부를 다른 표현으로 바꾸거나 덜어내세요.
5. 시대착오적 묘사: 본문 안의 시간 정보와 명백히 모순되는 소품·행동 묘사가
   있으면, 사실 주장을 새로 만들지 않는 범위에서 모순이 사라지게 다듬으세요.

고치지 말 것:
- 사실 관계·사건 순서·문단 구성(문단 추가/삭제/합치기 금지).
- 문체와 어조(교정 대상이 아닌 문장은 한 글자도 바꾸지 마세요).
- 근거 태그([E1] 등)가 남아 있으면 위치 그대로 유지하세요.

출력: 교열이 끝난 본문 전문만 출력하세요. 고친 부분 목록, 설명, 안내 문구,
마크다운 문법(**굵게**, ### 제목, - 목록 등)을 붙이지 마세요 — 순수 텍스트
줄바꿈과 문단 구분만 유지된 본문이어야 합니다.
"""


def build_chapter_proofread_prompt(
    *, chapter_content: str, overused_terms: list[str] | None = None
) -> list[dict[str, str]]:
    """overused_terms: 서비스 레이어가 본문에서 결정론적으로 센 고빈도 표현 목록
    (autobiography_service._count_overused_terms) — "같은 표현 3회 이상 완화"라는
    일반 지시만으로는 실제 남발('덤' 4회 잔존, 2026-07-18 실측)이 잡히지 않아,
    구체적인 단어를 지목해 전달한다."""
    sections = [f"[챕터 본문]\n{chapter_content}"]
    if overused_terms:
        terms_block = ", ".join(overused_terms)
        sections.insert(
            0,
            "[완화 대상 반복 표현 — 본문에서 4회 이상 등장한 단어들입니다. 각각 1~2회만 "
            f"남기고 나머지는 다른 표현으로 바꾸거나 덜어내세요]\n{terms_block}",
        )
    return [
        {"role": "system", "content": CHAPTER_PROOFREAD_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


UNITY_REVISION_SYSTEM_PROMPT = """\
전체 챕터와 스타일 바이블을 함께 검토해 인접 챕터 경계부의 어조·문체 단절을
매끄럽게 다듬으세요. 각 챕터의 첫 문장·마지막 문장 같은 "이음매" 부분이
자연스러운 전환처럼 읽히도록 연결어·전환 문장을 다듬는 것도 포함됩니다(단,
이때도 사실 관계나 사건 순서는 절대 바꾸지 마세요 — 문장 표현만 다듬는
것입니다). 사건의 사실 관계나 순서는 변경하지 마세요 — 오직 문체
통일성만 개선하는 윤문입니다.

화자가 실존 인물이거나 유명인과 이름이 같더라도, 당신이 사전 학습이나 검색으로
알고 있는 그 인물의 실제 정보와 원고 내용이 다르게 보인다는 이유로 "바로잡지"
마세요 — 이 원고 안에서 사실로 인정되는 것은 오직 원고에 이미 적힌 내용뿐이며,
당신의 배경지식과 대조해 날짜·장소·인물 관계 등을 고치는 것은 윤문의 범위를
벗어난 임의 개작입니다.

본문 중간에 `=== PART N: 제목 ===` 형식의 표시가 있다면, 이는 목차 설계
단계에서 의도적으로 나눈 Part(대분류) 경계를 알려주는 것입니다. 같은 Part
안에서 챕터가 바뀌는 지점은 위 지침대로 매끄럽게 다듬으세요. 하지만 이
표시가 있는 지점은 다르게 다루세요 — 시간의 도약, 배경의 전환, 국면의
전환처럼 의도적인 구조적 단절이므로 억지로 이어붙이지 말고, 오히려 그 전환이
독자에게 분명하게 느껴지도록 유지하거나 필요하면 더 뚜렷하게 다듬으세요.

출력 형식 — 반드시 지킬 것:
- 윤문이 끝난 전체 원고 본문만 그대로 출력하세요. "**수정된 원고**", "아래는 수정
  본입니다" 같은 안내 문구나 지시사항을 되뇌는 메타 설명을 앞뒤에 절대 붙이지
  마세요 — 이 출력이 그대로 최종 원고로 저장되어 PDF에 인쇄됩니다.
- 각 챕터 시작 직전에 있는 `<<<CHAPTER N>>>` 줄은 챕터 경계 마커입니다. 출력에서도
  각 챕터가 시작되는 지점에 정확히 같은 마커 줄을 그대로 유지하세요 — 개수와 순서가
  입력과 완전히 동일해야 하며, 마커를 빠뜨리거나 새로 만들면 안 됩니다(시스템이 이
  마커로 윤문된 본문을 챕터별로 다시 나눠 저장합니다). 마커 바로 다음 줄의
  `[N장. 제목]` 헤더 줄도 그대로 유지하세요.
- `=== PART N: 제목 ===` 표시는 당신에게 구조를 알려주기 위한 안내용일
  뿐입니다. 최종 출력 어디에도 이 표시나 이와 비슷한 마커를 그대로 남기지
  마세요 — Part가 바뀌었다는 사실은 문장의 흐름과 전환 자체로 드러내야지,
  표시로 나타내면 안 됩니다(챕터 경계 마커 `<<<CHAPTER N>>>`와 챕터 헤더만 예외).
- 마크다운 문법(**굵게**, ### 제목, > 인용, - 목록 등)을 쓰지 마세요. 입력 원고에
  이미 마크다운이 섞여 있다면 윤문하면서 순수 텍스트로 정리하세요.
"""


def build_unity_revision_prompt(*, style_bible: str, full_manuscript: str) -> list[dict[str, str]]:
    user_prompt = f"[스타일 바이블]\n{style_bible}\n\n[전체 원고]\n{full_manuscript}"
    return [
        {"role": "system", "content": UNITY_REVISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
