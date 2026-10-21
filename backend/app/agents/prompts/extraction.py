"""Extraction domain operations."""
from __future__ import annotations



from typing import Any


PROSE_REASSEMBLY_SYSTEM_PROMPT = """\
아래는 한 인터뷰 세션의 대화 로그 원문입니다. 이를 화자의 1인칭 산문으로 재조립하세요.

엄격한 제약:
- 문장의 병합·재배열·요약을 하지 마세요. 있는 내용을 빼거나 새 내용을 더하지
  마세요. (아래 예외 1, 2에 해당하는 경우만 예외입니다.)
- 어미와 추임새("음...", "그때가...")만 다듬어 자연스러운 문어체로 정돈하세요.
- 화자 특유의 말투와 표현은 최대한 보존하세요.
- assistant 턴은 어떤 형태든(질문뿐 아니라 "말씀해주셔서 감사해요", "다음 이야기로
  넘어가 볼까요?" 같은 인터뷰 진행자의 맞장구·감사 인사·화제 전환 멘트까지 전부)
  산문 결과물에 그대로 옮겨 쓰지 마세요. 오직 사용자(user) 턴의 내용만 이어 붙이세요.
- 사용자 턴이라도 "넘어가자", "그만할래요", "없어요"(마무리 확인 질문에 대한
  답), "네 계속하세요"처럼 사건 자체에 대한 서술이 아니라 대화 진행 자체를
  가리키는 발화(순수한 세션 진행 신호)는 산문에 포함하지 마세요 — 자서전을 읽는
  독자는 인터뷰가 어떻게 진행됐는지가 아니라 화자의 삶에 대해서만 알면 됩니다.
- 예외 1: 사용자 답변이 "서울대"처럼 한두 단어뿐이라 그 문장만으로는
  무엇에 대한 이야기인지 알 수 없으면, 바로 앞 assistant 질문에서 자연스럽게
  드러나는 주어/맥락만 최소한으로 보태 완전한 문장으로 만드세요(예: 질문
  "대학을 어디 다녔나요?" + 답변 "서울대" → "나는 서울대학교에 다녔다"). 질문에
  없던 새로운 사실을 지어내거나 답변 자체의 의미를 바꾸면 안 됩니다 — 문장을
  완성하는 데 꼭 필요한 최소한의 맥락만 보태는 것이며, 이 경우에도 assistant의
  질문 문장 자체를 산문에 그대로 옮겨 적으면 안 됩니다.
- 예외 2: 사용자가 이미 서술한 사건에 대해 assistant가 필수 정보(장소·시기·
  누구와·감정 등)를 되물었고 사용자가 그 속성만 답한 경우, 원래 사건을 서술한
  부분을 그 속성이 자연스럽게 녹아든 문장으로 고쳐 쓰세요("고쳐 쓰기"이지
  "덧붙이기"가 아닙니다). 원래 서술이 여러 문장에 걸쳐 있었다면, 그중 그
  속성과 실제로 관련된 문장 하나를 찾아 그 문장 안에 속성을 녹여 넣으세요 —
  나머지 문장은 그대로 두고, 답변을 별도의 새 문장으로 뒤에 덧붙이면 안
  됩니다. 결과물에는 병합된 문장만 남아야 하고, 병합되기 전의 원래 문장을
  그대로 또 남겨두면 절대 안 됩니다 — 같은 사건을 두 번 말하는 것은 "내용
  중복"이며 이 역시 금지 대상입니다.

  예시 1 — 사용자가 "나는 사과를 먹었다"라고 말한 뒤, assistant의 "언제, 어디서
  있었던 일인가요?"라는 질문에 "집에서, 21세"라고 답한 경우:
  * 틀린 결과 (하지 마세요): "나는 사과를 먹었다. 나는 21살 때 집에서 사과를
    먹었다." → 같은 사건이 두 문장으로 중복되어 있으므로 틀렸습니다.
  * 맞는 결과: "나는 21살 때 집에서 사과를 먹었다." → 한 문장으로만 병합되어
    있으므로 맞습니다.

  예시 2 — 사용자가 "집 밖에는 공원이 있었다. 나는 어릴 때 여기서 많이
  놀았다."라고 두 문장으로 말한 뒤, assistant의 "누구와 노셨나요?"라는 질문에
  "그냥 우리 아파트 살던 애들과 놀았다"라고 답한 경우:
  * 틀린 결과 (하지 마세요): "집 밖에는 공원이 있었다. 나는 어릴 때 여기서
    많이 놀았다. 그냥 우리 아파트 살던 애들과 놀았다." → "누구와" 답변이
    독립된 세 번째 문장으로 그냥 덧붙기만 했으므로 틀렸습니다.
  * 맞는 결과: "집 밖에는 공원이 있었다. 나는 어릴 때 여기서 우리 아파트
    살던 애들과 많이 놀았다." → "놀았다"라는 문장 안에 "누구와"가 자연스럽게
    녹아들었으므로 맞습니다. 공원이 있었다는 첫 문장은 이 속성과 무관하므로
    그대로 둡니다.

  예시 3 — 사용자가 "우리 가족만의 미니 올림픽을 열었다."라고 말한 뒤,
  assistant의 "그 미니 올림픽은 어떤 방식이었나요?"라는 질문에 "수영 대회,
  달리기 대회 같은 방식이었다"라고 답한 경우:
  * 틀린 결과 (하지 마세요): "우리 가족만의 미니 올림픽을 열었다. ... 수영
    대회, 달리기 대회 같은 방식이었다." → "방식" 답변이 무엇을 가리키는지
    알 수 없는 독립 문장으로 뚝 떨어져 붙었으므로 틀렸습니다. "장소·시기·
    누구와·감정"처럼 속성이 짧은 경우뿐 아니라, 이렇게 "어떤 방식/어떻게"
    같은 방식·형태를 묻는 후속 질문의 답변도 반드시 원래 문장에 병합해야
    합니다 — 이런 유형도 예외 2의 적용 대상입니다.
  * 맞는 결과: "우리 가족만의 미니 올림픽(수영 대회, 달리기 대회)을 열었다."
    또는 "수영 대회, 달리기 대회 같은 방식으로 우리 가족만의 미니 올림픽을
    열었다." → "미니 올림픽"이라는 원래 문장 안에 "방식" 정보가 녹아들어야
    맞습니다.

  이때도 사용자가 실제로 답한 속성만 병합에 사용하고 새로운 사실을 지어내면
  안 되며, assistant의 질문 문장 자체를 산문에 그대로 옮겨 적으면 안 됩니다.
  서로 다른 사건에 대한 문장들을 하나로 합치거나 요약하는 것은 여전히
  금지됩니다 — 이 예외는 같은 사건의 속성을 보충하는 후속 답변에만 적용됩니다.

- 예외 3: 사용자가 후속 질문에 "모르겠다", "기억이 안 난다", "잘 모름"처럼
  실질적인 정보 없이 답하지 못했음을 밝히는 경우, 그 답변은 병합도, 새 문장
  으로 덧붙이는 것도 하지 마세요 — 원래 문장을 그대로 두고 그 답변 자체는
  산문에서 완전히 생략하세요. "모르겠습니다"라는 진술 자체는 자서전 문장으로서
  아무 정보도 담지 않으므로 남길 이유가 없고, 억지로 남기면 assistant의 질문
  어미가 그대로 딸려와("~하셨는지 모르겠습니다"처럼 화자가 자기 자신에게
  존댓말을 쓰는 것 같은) 문장이 되기 쉽습니다.

  안전장치: 원래 서술이 여러 문장으로 길고 복잡해서 답변을 어느 문장에 녹여
  넣어야 할지 확신이 서지 않으면, 절대 억지로 아무 문장에나 끼워 넣지 마세요
  — 잘못된 문장에 잘못 끼워 넣으면 시점이나 맥락이 뒤바뀌는 왜곡이 생깁니다
  (예: 서울에 도착한 뒤의 감정을 대구에서 일하던 시절 이야기에 잘못 붙이는 것).
  확신이 없을 때는 융합을 포기하고, 답변을 새 문장으로 맨 끝에 덧붙이되, 이
  경우에도 예외 1과 마찬가지로 그 문장만 읽었을 때 무엇에 대한 답인지 알 수
  있도록 최소한의 주어/맥락을 반드시 보태세요 — "수영 대회, 달리기 대회 같은
  방식이었다."처럼 무엇의 방식인지 알 수 없는 문장을 뚝 떼어 붙이면 안 되고,
  "미니 올림픽은 수영 대회, 달리기 대회 같은 방식으로 진행되었다."처럼 원래
  화제(직전 문장에서 언급된 대상)를 주어로 되살려야 합니다. 이때도 물론 답변
  내용을 중복해서 두 번 쓰면 안 됩니다. 즉 "자신 있게 맞는 위치를 찾았을
  때만 융합하고, 아니면 맥락을 보탠 채로 안전하게 덧붙인다"가 원칙입니다.
  왜곡보다는 다소 어색한 나열이 낫지만, 맥락 없는 문장 조각을 그대로 붙이는
  것은 나열이 아니라 또 다른 오류입니다.
"""


def build_prose_reassembly_prompt(*, chat_turns: list[dict[str, str]]) -> list[dict[str, str]]:
    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in chat_turns)
    return [
        {"role": "system", "content": PROSE_REASSEMBLY_SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]


DISTORTION_CHECK_SYSTEM_PROMPT = """\
아래 [원본 발화]는 인터뷰에서 화자가 실제로 한 말이고, [재조립본]은 그 발화를
자연스러운 1인칭 산문으로 정리한 것입니다. 재조립본을 문장 단위로 검토해서,
원본에 실제로 없는 내용을 담은 문장을 모두 찾아 flags 배열에 담으세요.

flags에 담지 않는 것 (문제 없음):
- 문장을 자연스럽게 잇거나 순서를 다듬은 것
- 구어체를 문어체로 다듬은 것 (예: "그랬어요" → "그랬다")
- 원본에 있는 사실을 그대로 풀어 쓴 것 (표현이 살짝 바뀌어도 뜻이 같으면 문제 없음)

flags에 담아야 할 것 (지어낸 것):
- 원본에 없는 새로운 인물, 대사(따옴표 발언), 날짜, 장소, 사건, 결과의 추가
- 화자가 하지 않은 말을 한 것처럼 인용
- 원본과 모순되는 진술

애매하면 flags에 담으세요 — 이 재조립본은 화자의 말만 담아야 하는 축어
자료이므로, 감각적 묘사나 내적 성찰 같은 문학적 정교화라도 원본에 없는 새
내용이면 봐주지 마세요(챕터 집필 단계와는 판정 기준이 다릅니다).

sentence 필드에는 재조립본에서 문제가 된 문장을 그대로(축어) 인용하세요 —
이후 수리 단계가 이 문자열로 재조립본에서 해당 위치를 찾아 그 부분만 고치는
데 씁니다. 문제 있는 부분이 전혀 없으면 flags를 빈 배열로 두세요.
"""


DISTORTION_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {
                        "type": "string",
                        "description": "원본에 없다고 판단된 문장(재조립본에서 그대로 인용)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "어떤 새로운 사실/사건/대사가 근거 없이 추가됐는지",
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


def build_distortion_check_prompt(
    *, original_text: str, reassembled_prose: str
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": DISTORTION_CHECK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"[원본 발화]\n{original_text}\n\n[재조립본]\n{reassembled_prose}",
        },
    ]


DISTORTION_REPAIR_SYSTEM_PROMPT = """\
아래 [재조립본]은 [원본 발화]를 1인칭 산문으로 정리한 것인데, [수정 대상]
목록에 지목된 문장들에서 문제가 발견됐다고 합니다. 각 항목에는 문제가 된
문장과 그 사유가 적혀 있습니다. 하지만 그 지적이 틀렸을 수도 있습니다 —
판정자도 실수할 수 있으므로, 항목마다 그대로 믿지 말고 직접 확인하세요.

항목마다 0단계 — 먼저 반드시 확인할 것: 그 문장이 가리키는 내용을 [원본
발화] 전체에서 실제로 찾아보세요(표현이 살짝 다듬어졌을 수 있으니 뜻이
같은지로 판단하세요). **[원본 발화]에 그 내용(또는 같은 뜻의 내용)이 실제로
있다면, 그 항목은 오탐입니다 — 그 문장은 절대 삭제하거나 고치지 말고 그대로
두세요.**

확인 결과 지적이 정말 맞은 항목만(그 내용이 원본 어디에도 없는 경우만)
아래 방법으로 고치세요:
1. [원본 발화]에 실제로 있는 내용으로 바꿔 쓸 수 있으면, 원본에 맞게 고쳐
   쓰세요(문체와 흐름은 주변 문장과 자연스럽게 이어지도록).
2. 원본에 대응하는 내용이 전혀 없으면 그 문장을 삭제하고, 필요하면 앞뒤
   문장을 최소한으로 다듬어 흐름이 끊기지 않게 하세요.

반드시 지킬 것:
- [수정 대상]에 없는 문장은 한 글자도 바꾸지 마세요 — 이 작업은 전면
  재작성이 아니라 국소 수술입니다. 지목되지 않은 내용을 요약하거나
  생략하거나 표현을 바꾸지 마세요.
- 고치면서 [원본 발화]에 없는 새로운 인물·사건·대사·날짜를 추가하지 마세요.
- 완성된 재조립본 전체만 출력하세요. 안내 문구나 수정 내역 설명을 출력에
  포함하지 마세요 — 이 출력이 그대로 session_prose로 저장됩니다.
"""


def build_distortion_repair_prompt(
    *, reassembled_prose: str, original_text: str, flags: list[dict[str, str]]
) -> list[dict[str, str]]:
    """flags: [{"sentence": 문제 문장, "reason": 사유}, ...] —
    DISTORTION_CHECK_SCHEMA로 판정한 결과를 그대로 넘긴다(build_chapter_repair_prompt의
    flagged_items와 같은 모양)."""
    flags_block = "\n".join(
        f"- 문장: {item['sentence']}\n  사유: {item['reason']}" for item in flags
    )
    return [
        {"role": "system", "content": DISTORTION_REPAIR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"[재조립본]\n{reassembled_prose}\n\n"
                f"[원본 발화]\n{original_text}\n\n"
                f"[수정 대상]\n{flags_block}"
            ),
        },
    ]


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
                        "description": "확정 연도가 아니면 상대적 표현 허용 (예: '고등학교 시절'). "
                        "반드시 한국어로 쓰세요.",
                    },
                    "estimated_year_start": {
                        "type": ["integer", "null"],
                        "description": "이 사건이 시작된 것으로 추정되는 서기 연도(예: 1963). "
                        "본문의 명시적 연도·나이·시기 표현에서 합리적으로 추정 가능할 때만 채우고, "
                        "근거가 전혀 없으면 null. 챕터 시간 범위 강제·시기 정렬에 쓰인다.",
                    },
                    "estimated_year_end": {
                        "type": ["integer", "null"],
                        "description": "이 사건이 끝난 것으로 추정되는 서기 연도. 단일 시점 사건이면 "
                        "estimated_year_start와 같은 값, 추정 불가면 null.",
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
                    "estimated_year_start", "estimated_year_end",
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

모든 문자열 출력(one_line_summary, occurred_at_label, place, people 등)은 예외
없이 한국어로 쓰세요 — 산문에 외국 지명·인명이 나와도 라벨 표기는 한국어입니다
(예: "age 21 onward"가 아니라 "21살 이후").

estimated_year_start/end(추정 서기 연도)는 본문의 명시적 연도("1963년"), 나이
표현("스물한 살 때" — 출생 정보가 본문에 있으면 환산), 명확한 시기 표현에서
합리적으로 추정 가능할 때만 채우세요. 근거 없는 추측은 금지 — 불확실하면 null.

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
