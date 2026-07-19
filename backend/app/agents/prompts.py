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
  7. 사진 캡션/텍스트(Azure Vision) 1차 타당성 검증 + PHOTO 세션 오프닝 질문 생성
  8. Phase 3: 스타일 바이블 / 이벤트 병합 판정
  9. Phase 4: 동적 목차 / 하향식 집필 / 통일성 윤문 / 팩트체크 / 제3자 위해성 분류 / NER 스캔
  10. Phase 3 중요도 스코어링: 생애 이정표 카테고리 매칭(결정론적 키워드 분류)
  11. 자서전 커스터마이징: 말투/구성/컨셉 선택지, 샘플 미리보기, 질문 태그 기반 및
      콘텐츠 기반 추천
"""

from __future__ import annotations

from collections import Counter
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

이 자서전은 화자 본인의 것입니다 — 사건 요약에 다른 사람(존경하는 인물, 롤모델 등)이
등장하더라도 슬롯(장소·시기·감정 등)은 항상 화자 본인 기준으로 물으세요. 그 인물
본인이 무엇을 겪었는지는 화자가 답할 수 없으니 묻지 마세요.

질문은 반드시 사용자가 방금 말한 내용 속의 구체적인 단어·표현을 가져와 "무엇을
묻는 것인지" 분명하게 밝히세요. "그때", "그 일", "그 사건", "그거"처럼 무엇을
가리키는지 불분명한 대명사로 뭉뚱그리는 질문은 절대 만들지 마세요 — 사용자가
이미 구체적으로 말했는데도 지칭이 모호한 질문을 다시 던지면 성의 없이 듣는
것처럼 느껴집니다.
- (X) "그때는 어떤 일이 있었나요?"
- (O) "그 이직 준비하실 때 제일 힘드셨던 부분은 구체적으로 어떤 거였어요?"

"시기"(time) 슬롯을 물을 때는 "언제쯤이었어요?", "그때가 언제였나요?"처럼
두루뭉술하게 묻지 말고, 반드시 나이나 특정 연도처럼 숫자로 답할 수 있게
물으세요(예: "그때 몇 살쯤이셨어요?", "몇 년도쯤이었는지 기억나세요?").

사용자의 답변이 길고 여러 소재가 섞여 있다면, 그 전체를 뭉뚱그려 묻지 말고 그
안에서 이 사건의 핵심이 되는 한 문장이나 대목을 먼저 짚어내고, 정확히 그 부분을
가리키며 질문하세요.

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
한꺼번에 묻지 말고 질문 하나만 짧게 던지세요.

답변에 다른 사람(존경하는 인물 등)이 등장하더라도 "그 순간의 속마음"은 항상
화자 본인의 것을 물어야 합니다 — 그 인물 본인의 속마음이나 경험을 캐묻지 마세요.

질문은 반드시 사용자가 방금 말한 내용 속의 구체적인 단어·장면을 가져와 물으세요.
"그때", "그 일"처럼 무엇을 가리키는지 불분명한 대명사로 뭉뚱그리지 마세요 — 답변이
길다면 그 안에서 가장 인상적인 한 대목을 짚어 그 장면에 대해 물으세요.

이 텍스트는 채팅 화면에 그대로 표시되니 마크다운 문법 없이 순수한 대화체
문장으로만 답하세요.
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

이 자서전은 화자 본인의 것입니다 — 절대 잊지 마세요:
- 화자가 대화 중 다른 사람(예: 존경하는 인물, 롤모델, 유명인)을 언급했더라도,
  질문은 반드시 화자 본인의 경험·감정·행동으로 초점을 두거나 되돌려야 합니다.
  그 인물 본인이 무엇을 겪었는지, 그 인물의 개인사가 구체적으로 어땠는지는
  화자가 답할 수 없는 질문이니 절대 묻지 마세요.
- 대신 "그 이야기를 알게 된 뒤 화자님은 어떻게 하셨나요", "그 다짐 이후 실제로는
  어떻게 됐나요"처럼, 그 인물이 화자 자신의 삶에 미친 영향·화자의 다음 행동으로
  질문을 돌리세요.
- 예시 1: 화자가 "나는 젠슨 황을 동경했다. 그가 맥도날드 알바로 고생했다는 이야기에
  영향받아 나도 기업가가 되겠다 다짐하고 미국으로 갔다"고 답했다면,
  * 틀린 질문(하지 마세요): "젠슨 황이 맥도날드에서 구체적으로 어떤 어려움을
    겪었나요?" → 화자가 아니라 제3자에게 물어야 할 질문입니다.
  * 맞는 질문: "미국에 가서 처음엔 어떠셨어요?" 또는 "그 다짐이 실제로 어떻게
    이어졌나요?" → 화자 본인의 이후 경험을 묻습니다.

- 예시 2 (특히 조심할 함정): 화자가 자신의 아버지·어머니 같은 가족의 고생담을
  존경심을 담아 길게 이야기하면, "그분이 구체적으로 어떤 고생을 하셨는지 더
  듣고 싶다"는 유혹이 특히 강하게 듭니다 — 그런 이야기일수록 반드시 더 조심해서
  화자 본인에게로 초점을 되돌리세요. 화자가 "6.25 전쟁통에 홀로 남으로 내려온
  아버지가 막노동을 전전하며 삼남매를 대학까지 보내셨다. 힘든 내색 한 번 안
  하셨다. 나는 그런 아버지를 보며 가족을 지켜야 한다는 걸 배웠다"고 답했다면,
  * 틀린 질문(하지 마세요): "아버지가 전쟁통에 구체적으로 어떤 어려움을
    겪으셨고, 그때 어떤 선택을 하셨나요?" → 아버지 본인의 경험이니 화자가
    답할 수 없거나, 답하더라도 전해 들은 이야기의 재탕일 뿐입니다.
  * 맞는 질문: "그런 아버지를 보며 배운 걸 실제로 삶에서 어떻게 실천하셨나요?"
    처럼, 화자 자신이 그 영향을 받아 무엇을 하고 어떻게 느꼈는지를 묻습니다.

- 그런 지점이 있으면 has_followup=true로, 위 원칙을 지키며 그 지점을 자연스럽게
  파고드는 질문 하나를 question에 담으세요.
- 없으면(이미 충분히 다뤄졌거나 더 캐물을 만한 여지가 없으면) has_followup=false로
  하고 question은 null로 두세요 — 없는데 억지로 만들어내면 안 됩니다. 애매하면
  false를 고르세요(과도한 캐묻기가 더 큰 문제입니다).
- 대화가 길어 여러 소재가 섞여 있다면, 전체를 뭉뚱그려 묻지 말고 그중 가장
  핵심이 되는 한 문장이나 사건을 먼저 골라낸 뒤, 정확히 그 부분을 겨냥해
  질문하세요.
- 질문은 반드시 화자가 실제로 언급한 구체적인 단어·장면을 가져와 무엇을 묻는지
  분명히 밝히세요. "그때", "그 일", "그 사건"처럼 무엇을 가리키는지 불분명한
  대명사로 뭉뚱그리는 질문은 만들지 마세요.
- 시기(연도·나이 등)에 대해 캐묻는 경우, "그게 언제쯤이었나요?"처럼 두루뭉술하게
  묻지 말고 나이나 특정 연도처럼 숫자로 답할 수 있게 물으세요.
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


# 매 턴 통합 게이팅 — 감정 세이프가드 1층 판정(TIER1_DETECTION)과 슬롯 게이팅
# (SLOT_GATING)은 매 사용자 턴마다 각각 별도 LLM 호출로 돌던 것을 하나로 합쳤다
# (2026-07-18 — 턴당 지연·비용 절반). 두 원본 프롬프트를 그대로 이어붙여 단일
# 진실 원천을 유지한다(각 기준이 바뀌면 이 통합본도 자동 반영). 원본 프롬프트/
# 스키마는 샌드박스 개별 튜닝용으로 남겨둔다.
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


# 왜곡 탐지 (event_extraction_service._passes_distortion_check) — 재조립본이 원본
# 발화에 없는 사실을 지어내지 않았는지 판정한다. GROUNDEDNESS_JUDGE_SYSTEM_PROMPT(챕터
# 집필 근거검증)와 목적은 같지만 기준은 더 엄격하다 — 챕터 집필은 "문학적 정교화"를
# 의도적으로 허용하지만, 이 재조립본은 화자의 말만 최소 변형으로 옮긴 축어 자료여야
# 하므로(PROSE_REASSEMBLY_SYSTEM_PROMPT) 정교화 자체를 봐줄 이유가 없다 — 그래서
# GROUNDEDNESS_JUDGE_SYSTEM_PROMPT의 "감각 묘사·내적 독백은 통과" 예외를 두지 않는다.
# 출력 계약을 JSON 스키마가 아니라 단문 프로토콜(PASS / FAIL: 사유)로 둔 이유는
# clients/groundedness.py와 같다 — solar-mini의 Structured Outputs 지원 여부가
# solar-pro3처럼 실측 검증된 적이 없어, 검증된 단문 프로토콜만 쓴다.
DISTORTION_CHECK_SYSTEM_PROMPT = """\
아래 [원본 발화]는 인터뷰에서 화자가 실제로 한 말이고, [재조립본]은 그 발화를
자연스러운 1인칭 산문으로 정리한 것입니다. 재조립본이 원본에 실제로 없는
내용을 지어내지 않았는지 판정하세요.

지어낸 것으로 보지 않는 것 (통과):
- 문장을 자연스럽게 잇거나 순서를 다듬은 것
- 구어체를 문어체로 다듬은 것 (예: "그랬어요" → "그랬다")
- 원본에 있는 사실을 그대로 풀어 쓴 것

지어낸 것으로 판정해야 할 것 (실패):
- 원본에 없는 새로운 인물, 대사(따옴표 발언), 날짜, 장소, 사건, 결과의 추가
- 화자가 하지 않은 말을 한 것처럼 인용
- 원본과 모순되는 진술

애매하면 지어낸 것 쪽으로 판정하세요 — 이 재조립본은 화자의 말만 담아야 하는
축어 자료이므로, 감각적 묘사나 내적 성찰 같은 문학적 정교화라도 원본에 없는
새 내용이면 봐주지 마세요(챕터 집필 단계와는 판정 기준이 다릅니다).

재조립본에 원본에 없는 내용이 전혀 없으면 정확히 PASS 한 단어만 출력하세요.
하나라도 있으면 FAIL: 뒤에 어떤 부분이 어떤 이유로 문제인지 한국어로 한 문장
안에 쓰세요. 그 외의 형식은 출력하지 마세요.
"""


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


# --------------------------------------------------------------------------- #
# 7. 사진 캡션/텍스트 1차 타당성 검증 + PHOTO 세션 오프닝 질문 생성              #
# --------------------------------------------------------------------------- #

# Azure Vision의 Caption 기능(자연어 한 문장 요약)은 일부 지역에서만 지원되고
# 그마저도 영어로만 나와(app/clients/azure_vision.py 모듈 docstring 참조,
# 2026-07-16 실제 여러 지역에서 재현) 쓰지 않기로 했다. 대신 지역 제약이 없는
# objects(물체 탐지)/tags(장면 태그)를 쓰는데, 그 결과값 자체가 영어 키워드
# 목록이라(예: "person", "outdoor", "grass") 사용자에게 그대로 보여줄 수 없다 —
# 이 프롬프트가 그 영어 키워드 목록을 자연스러운 한국어 명사구 하나로 다듬는다.
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


# EPISODE 세션(대시보드 "에피소드 추가", 2026-07-16) — 자동 배정 큐(고정 질문/사진)와
# 무관하게 사용자가 직접 시작하는 자유 서술 세션. build_photo_session_opening과 같은
# 이유로 Solar 호출 없는 순수 문자열이다(세션 시작은 가볍게, 실제 대화 내용은 이후
# 일반 인터뷰와 동일하게 슬롯 게이팅·꼬리질문이 적용된다).
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


# --------------------------------------------------------------------------- #
# 8. Phase 3: 스타일 바이블 / 이벤트 병합 판정                                  #
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# 9. Phase 4: 동적 목차 / 하향식 집필 / 통일성 윤문 / 팩트체크 / 제3자 위해성   #
# --------------------------------------------------------------------------- #

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
있는 범위를 넘지 않아야 합니다.
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
말고 감정·주제 흐름만 추상적으로 서술하세요.

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
아래 자료를 바탕으로 챕터 본문을 집필하세요. [스타일 바이블]의 문체를 따르고,
[전체 시놉시스]와 [챕터 시놉시스]의 설계를 벗어나지 마세요.

분량: 이 챕터는 4,000~6,000자(공백 포함) 분량으로, 얇은 문고본이 아니라
실제 출간 자서전 한 챕터만큼 충분히 상세하게 쓰세요. 분량은 사건을 많이
나열해서가 아니라, 중심 장면 3~5개를 골라 장면당 4~8문단으로 깊이 펼쳐서
채우세요. 출력을 마치기 전에 본문이 4,000자에 못 미친다고 판단되면, 새
사건을 추가하지 말고 이미 쓴 장면을 더 깊이 파고들어(감각, 행동의 세부,
내적 성찰) 분량을 채우세요.

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
- "그리고 -했다. 그리고 -했다" 식의 사건 나열이나 요약형 진술로 열지 말고,
  구체적인 장면·감각(공간, 소리, 냄새, 몸짓)으로 문을 여세요.
- 감정을 이름 붙여 설명("슬펐다", "기뻤다")하기보다, 행동과 디테일로 그 감정이
  드러나게 하세요(보여주기, showing not telling).
- 문단 연결: 각 문단은 바로 앞 문단에서 인과, 시간의 흐름, 정서적 여운 중
  하나로 이어져야 합니다. 서로 다른 사건을 다루는 문단이라도 독립된 장면의
  나열이 아니라 하나의 연속된 흐름으로 읽혀야 합니다. 이를 위한 연결
  문장·전환 문단(새로운 사실 주장이 없는)은 자유롭게 쓰세요 — 근거 태그도
  필요 없습니다.
- 시점·어조 일관: 처음부터 끝까지 1인칭("나")을 유지하세요 — 자기 자신을
  3인칭("그", 이름)으로 부르는 문단이 섞이면 안 됩니다. 종결어미는 [스타일
  바이블]의 "종결어미 규정"이 지정한 하나로 챕터 끝까지 유지하세요(사용자가
  선택한 말투 지시가 있으면 그쪽이 우선).
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


CHAPTER_EXPANSION_SYSTEM_PROMPT = """\
아래 [챕터 초안]은 목표 분량(4,000~6,000자)에 크게 못 미칩니다. 초안의 사실
관계·사건 순서·문단 구조를 그대로 유지하면서, 이미 있는 장면들을 더 깊이
파고들어 분량을 목표 범위까지 늘리세요.

확장 방법 — 반드시 지킬 것:
- 새로운 사건·인물·사실을 추가하지 마세요. [근거 사건 목록]에 없는 내용은
  이 확장에서도 여전히 금지입니다.
- 늘리는 수단은 오직 정교화입니다: 공간·소리·냄새·몸짓 같은 감각 묘사,
  행동의 세부, 내적 성찰, 감정의 결. 이미 요약된 문장을 장면으로 풀어 쓰세요.
- 초안에 있는 근거 태그([E1] 등)는 해당 문단 확장 후에도 문단 끝에 그대로
  유지하세요.
- 문체·시점·종결어미는 초안과 동일하게 유지하세요.
- 완성된 산문 본문만 출력하세요. 안내 문구나 메타 설명을 붙이지 마세요.
"""


def build_chapter_expansion_prompt(
    *, chapter_content: str, source_events_text: str
) -> list[dict[str, str]]:
    user_prompt = (
        f"[근거 사건 목록]\n{source_events_text}\n\n"
        f"[챕터 초안]\n{chapter_content}"
    )
    return [
        {"role": "system", "content": CHAPTER_EXPANSION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
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
   격식(예: "-습니다"체)이 섞인 문장을 지배적인 쪽으로 통일하세요.
4. 표현 남발: 같은 단어·이미지가 세 번 이상 반복되면, 문장의 의미를 바꾸지
   않는 선에서 일부를 다른 표현으로 바꾸거나 덜어내세요.
5. 시대착오적 묘사: 본문 안의 시간 정보와 명백히 모순되는 소품·행동 묘사가
   있으면, 사실 주장을 새로 만들지 않는 범위에서 모순이 사라지게 다듬으세요.

고치지 말 것:
- 사실 관계·사건 순서·문단 구성(문단 추가/삭제/합치기 금지).
- 문체와 어조(교정 대상이 아닌 문장은 한 글자도 바꾸지 마세요).
- 근거 태그([E1] 등)가 남아 있으면 위치 그대로 유지하세요.

출력: 교열이 끝난 본문 전문만 출력하세요. 고친 부분 목록, 설명, 안내 문구를
붙이지 마세요.
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


# 근거 검증(Groundedness Check) — 예전에는 로컬 NLI(mDeBERTa) entailment로 문장
# 단위 대조를 했는데, 감각적 묘사·내적 성찰 같은 정당한 정교화까지 "원문을
# 논리적으로 함의하지 않는다"는 이유로 거의 전부 플래그되는 근본적인 도구 부적합
# 문제가 있었고, 512 토큰 제약 때문에 사건 여러 개를 함께 검증하려면 그룹핑이
# 필요해 챕터 하나에 20분 넘게 걸리는 속도 문제까지 겹쳤다(2026-07-17). 같은
# 목적(챕터 본문을 근거 자료와 대조)을 이미 Solar LLM 판정으로 하고 있는
# _run_factcheck/FACT_REEXTRACTION_SYSTEM_PROMPT와 동일한 패턴으로 교체한다 —
# 챕터 전체를 근거 사건 전체와 함께 한 번의 호출로 비교하므로 토큰 제약도,
# 그룹핑도 필요 없다.
GROUNDEDNESS_JUDGE_SYSTEM_PROMPT = """\
아래 [챕터 본문]이 [근거 사건 목록]에 실제로 근거하는지 판정하세요. 판정
기준은 딱 하나입니다 — "독자가 이 문장을 실제로 일어난 일에 대한 검증
가능한 사실 주장으로 받아들일까?" 그렇다면, 그리고 그 사실이 근거 사건
목록 어디에도 없다면 flag하세요. 문학적 표현이 사건 목록의 문장과 토씨
하나까지 같아야 한다는 뜻이 아닙니다 — 같은 사건을 풀어 쓴 것이면 표현이
아무리 구체적이고 감각적이어도 근거 있는 것입니다.

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


# 외과적 수리(Surgical Repair) — 팩트체크/근거검증에 걸린 문장만 고치는 재작성.
# 예전에는 플래그가 뜨면 플래그 내용을 전달하지도 않은 채 같은 프롬프트로 챕터
# 전체를 다시 쓰고(블라인드 재집필) 플래그 수가 적은 쪽을 채택했는데, 이는 (1)
# 무엇이 문제였는지 모델이 모른 채 다시 쓰는 동전 던지기라 수렴 보장이 없고,
# (2) 멀쩡했던 부분에서 새 환각이 생길 수 있으며, (3) 전체 재집필+재검증 비용이
# 통째로 든다. 플래그된 문장·사유·근거를 명시해 그 문장만 고치게 하면 세 문제가
# 모두 사라진다(2026-07-17 도입).
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


# 배치판 — write_chapter의 등장인물 스캔(character_service)이 챕터 하나에서 발견된
# 인물 전원을 단일 호출로 분류한다(2026-07-18 — 이전엔 인물마다 별도 호출이라
# 인물 수만큼 지연·비용이 곱해졌다). 단건판은 샌드박스 개별 튜닝용으로 유지.
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
        "example": "1948년 봄, 나는 작은 시골 마을에서 태어났다. 이 책은 그로부터 이어진 일흔 해의 기록이다.",
        "instruction": (
            "컨셉 지시: 생애 전반 회고록으로 집필하세요. 출생부터 현재까지 굵직한 궤적을 "
            "모두 담아내는 정통 자서전의 형태를 갖추세요. 후대나 가족에게 남기는 '가문의 "
            "공식 기록'으로서의 포괄적이고 묵직한 형태를 유지하세요."
        ),
    },
    "business": {
        "name": "비즈니스 & 리더십 에세이",
        "description": "사업 성공 스토리와 실무적 노하우에 집중. 위기 관리, 조직 운영, 경영 철학 등에 포커스를 맞춥니다.",
        "example": "첫 공장을 세우던 날 수중에 남은 것은 어음 몇 장뿐이었다. 그날 배운 원칙이 이후 30년 경영의 뼈대가 되었다.",
        "instruction": (
            "컨셉 지시: 비즈니스 & 리더십 에세이로 집필하세요. 개인적인 가정사나 감정 묘사는 "
            "최소화하고, 위기 관리, 조직 운영, 협상, 실패를 딛고 일어선 경영 철학 등 "
            "'프로페셔널'로서의 성취에 포커스를 맞추세요."
        ),
    },
    "family": {
        "name": "가족사 및 양육기",
        "description": "자녀 양육과 가족 간의 연대에 집중하는 감성 버전. 부모로서의 희로애락과 다음 세대에 물려줄 가치관을 담습니다.",
        "example": "아이의 첫 울음소리를 듣던 순간, 나는 비로소 내 부모의 마음을 이해하게 되었다.",
        "instruction": (
            "컨셉 지시: 가족사 및 양육기로 집필하세요. 부모로서의 희로애락, 자녀를 키우며 "
            "내면이 성장했던 과정, 다음 세대에게 물려주고 싶은 삶의 가치관을 따뜻한 시선으로 "
            "담아내세요."
        ),
    },
    "masterclass": {
        "name": "멘토링 대담집",
        "description": "가상의 후배나 대중이 던질 법한 질문에 현명하게 답변하는 인터뷰/토크쇼 형태입니다.",
        "example": "\"그때로 돌아간다면 같은 선택을 하실 건가요?\" — 글쎄요, 아마 또 그 길을 갔을 겁니다. 후회는 방향이 아니라 속도에 있었으니까요.",
        "instruction": (
            "컨셉 지시: 멘토링 대담집으로 집필하세요. 가상의 후배나 대중이 던질 법한 "
            "날카롭고 흥미로운 질문(예: '그때로 돌아간다면 같은 선택을 하실 건가요?')을 "
            "세팅하고, 현명하고 위트 있게 답변하는 '토크쇼' 형태를 취하세요."
        ),
    },
    "reporter": {
        "name": "3인칭 관찰자 평전",
        "description": "기자가 심층 취재하여 객관적 시각으로 서술한 듯한 형태. 나 자신을 '그/그녀'로 지칭합니다.",
        "example": "그는 항상 낡은 수첩을 들고 다녔다. 반세기의 기록이 그 수첩 속에 빼곡했다.",
        "instruction": (
            "컨셉 지시: 3인칭 관찰자 평전으로 집필하세요. 기자가 심층 취재하듯 나 자신을 "
            "'그' 또는 '그녀'로 지칭하며 제3자의 시선에서 묘사하세요. 자화자찬의 부담을 "
            "덜고 객관적인 신뢰감을 주세요."
        ),
    },
    "resilience": {
        "name": "실패와 재기의 기록",
        "description": "성공의 결과가 아니라, 인생에서 가장 처참했던 실패와 바닥을 쳤던 순간에 돋보기를 들이대는 버전입니다.",
        "example": "부도가 난 다음 날 아침에도 해는 떴다. 나는 그 해를 보며 다시 시작하기로 했다.",
        "instruction": (
            "컨셉 지시: 실패와 재기의 기록으로 집필하세요. '어떻게 성공했는가'보다 "
            "'어떻게 무너지지 않고 버텼는가'에 집중하세요. 가장 처참했던 실패와 바닥을 "
            "쳤던 순간을 중심으로, 강렬한 공감과 위로를 줄 수 있게 서술하세요."
        ),
    },
    "golden_era": {
        "name": "특정 시기 집중 조명",
        "description": "인생 전체를 다루지 않고, 내 삶을 가장 크게 변화시킨 '결정적인 시기'만 현미경처럼 들여다봅니다.",
        "example": "1997년부터 3년, 그 시간이 내 인생의 방향을 전부 바꿔놓았다. 이 책은 오직 그 시절의 기록이다.",
        "instruction": (
            "컨셉 지시: 특정 시기 집중 조명으로 집필하세요. 인생 전체를 다루지 말고, "
            "삶을 가장 크게 변화시킨 결정적인 시기의 밀도 높은 사건과 감정 변화를 "
            "소설처럼 쫀쫀하게 그려내세요."
        ),
    },
    "passion": {
        "name": "덕업일치 및 취미 몰입기",
        "description": "평생을 바친 특정 취미나 관심사를 매개로 인생을 풀어내는 에세이 형식입니다.",
        "example": "40년을 산에 다녔다. 정상보다 능선에서 배운 것이 더 많았고, 그것이 그대로 내 삶의 지혜가 되었다.",
        "instruction": (
            "컨셉 지시: 덕업일치 및 취미 몰입기로 집필하세요. 자신이 열정을 쏟았던 "
            "특정 취미·관심사를 중심으로, 그 안에서 얻은 통찰을 인생 전반의 지혜로 "
            "연결 짓는 매력적인 에세이 형식을 취하세요."
        ),
    },
    "philosophical": {
        "name": "가치관 및 철학 사전",
        "description": "사건의 나열이 아닌, 삶의 핵심 키워드에 대한 나만의 정의를 내리는 버전입니다.",
        "example": "돈 — 내게 돈은 목적이 아니라 온도였다. 있으면 따뜻하고 없으면 시렸지만, 삶의 방향을 정해준 적은 없었다.",
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
    """8개 샘플 중 하나의 조합에 대한 미리보기 텍스트를 생성한다. 각 축의
    instruction(추상 지시)뿐 아니라 example(예시 문장)도 few-shot으로 함께
    준다 — 추상적 지시문만으로는 문체 간 차이가 뚜렷하게 재현되지 않는다."""
    tone = TONE_OPTIONS[tone_key]
    structure = STRUCTURE_OPTIONS[structure_key]
    concept = CONCEPT_OPTIONS[concept_key]

    user_prompt = (
        f"[말투 지시]\n{tone['instruction']}\n"
        f"[말투 예시 — 어조만 참고, 내용은 가져오지 말 것] {tone['example']}\n\n"
        f"[구성 지시]\n{structure['instruction']}\n"
        f"[구성 예시 — 형식만 참고, 내용은 가져오지 말 것] {structure['example']}\n\n"
        f"[컨셉 지시]\n{concept['instruction']}\n"
        f"[컨셉 예시 — 관점만 참고, 내용은 가져오지 말 것] {concept['example']}\n\n"
        f"[스타일 바이블]\n{style_bible}\n\n"
        f"[사건 요약]\n{event_summaries}"
    )
    return [
        {"role": "system", "content": SAMPLE_PREVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# ── 11-5. 커스터마이징된 기존 프롬프트 빌더 변형 ─────────────────────────

# 구성(STRUCTURE_OPTIONS) 관점별로 Part(대분류)를 어떤 원리로 나눌지에 대한 힌트.
# "episodic"(결정적 에피소드 중심)은 이미 5~10개의 독립된 단편이 책 전체라 다시
# 3~5개 Part로 나누면 억지스럽다 — 별도 예외 처리(아래 build_customized_toc_prompt).
_PART_SHAPING_HINTS: dict[str, str] = {
    "thematic": (
        "삶을 관통하는 핵심 가치관·주제군을 Part 단위로 묶으세요. Part 순서 "
        "자체가 하나의 감정적·통찰적 상승 곡선(예: 정체성 형성 → 실패와 좌절 → "
        "재기와 성장)을 이루도록 배열하세요."
    ),
    "in_medias_res": (
        "정확히 3개의 Part로 구성하세요 — 1부: 현재(극적 순간), 2부: 과거로의 "
        "회상(사건의 씨앗), 3부: 현재로의 복귀(그 사이의 과정과 지금). 3부의 "
        "마지막 챕터는 1부에서 던진 장면·질문을 회수해야 합니다."
    ),
    "geographical": (
        "실제로 옮겨 다닌 주요 장소(지역·도시) 단위로 Part를 나누세요. 한 Part "
        "안에서는 같은 공간적 배경을 유지하고, Part가 바뀌는 지점에서 실제 "
        "이주·이동이 일어나야 합니다."
    ),
    "chronological": (
        "생애 단계(유년기/청년기/장년기/노년기 등)를 Part 경계로 삼으세요. "
        "Part가 바뀌는 지점은 곧 인생의 국면이 바뀌는 지점이어야 합니다."
    ),
}

_EPISODIC_PART_EXCEPTION = (
    "\n\n[Part 구성 예외] 결정적 에피소드 중심 구성은 이미 그 자체로 5~10개의 "
    "독립된 단편이 책 전체입니다 — 이를 다시 3~5개의 상위 Part로 묶으면 오히려 "
    "단편의 독립성을 해치고 어색한 상위 분류가 생깁니다. parts 배열에는 정확히 "
    "1개의 Part만 담고(part_index=1, part_title은 책 전체를 아우르는 제목), "
    "모든 챕터의 part_index를 1로 통일하세요."
)


def build_customized_toc_prompt(
    *,
    event_summaries_with_scores: str,
    structure_key: str,
) -> list[dict[str, str]]:
    """TOC_GENERATION_SYSTEM_PROMPT에 사용자가 선택한 구성(structure) 지시문을 주입한다.
    기존 build_toc_generation_prompt의 커스터마이징 확장판. instruction(추상 지시)뿐
    아니라 example(예시 목차)도 few-shot으로 함께 준다 — 추상적 설명만으로는 "주제별
    구성"과 "역순행적 구성" 같은 구조적 차이가 잘 재현되지 않는다."""
    structure = STRUCTURE_OPTIONS[structure_key]
    if structure_key == "episodic":
        part_instruction = _EPISODIC_PART_EXCEPTION
    else:
        hint = _PART_SHAPING_HINTS.get(structure_key, "")
        part_instruction = f"\n\n[Part 구성 지침 — {structure['name']}에 맞게]\n{hint}"

    system_prompt = (
        f"{TOC_GENERATION_SYSTEM_PROMPT}\n\n"
        f"[사용자가 선택한 목차 구성 방식]\n{structure['instruction']}\n\n"
        f"[구성 예시 — 형식만 참고할 것, 실제 내용은 사용자의 사건 요약을 따를 것]\n"
        f"{structure['example']}\n\n"
        f"반드시 이 구성 방식을 따라 목차 후보를 생성하세요. 3개 후보 모두 이 구성 "
        f"관점을 기반으로 하되, 세부 챕터 배분이나 제목에서 변주를 주세요."
        f"{part_instruction}"
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
    time_scope: str | None = None,
    other_chapter_titles: list[str] | None = None,
) -> list[dict[str, str]]:
    """CHAPTER_WRITING_SYSTEM_PROMPT에 말투(tone)·컨셉(concept) 지시문을 주입한다.
    기존 build_chapter_writing_prompt의 커스터마이징 확장판. instruction과 함께
    example(예시 문장)도 few-shot으로 주입한다 — 추상적 지시문만으로는 문체가
    충실히 재현되지 않는다(내용 유출을 막기 위해 "참고만 하라"는 경고를 함께 둠)."""
    tone = TONE_OPTIONS[tone_key]
    concept = CONCEPT_OPTIONS[concept_key]

    system_prompt = (
        f"{CHAPTER_WRITING_SYSTEM_PROMPT}\n\n"
        f"[사용자가 선택한 말투]\n{tone['instruction']}\n"
        f"[말투 예시 — 어조·문장 리듬만 참고하고, 예시의 구체적 사실·소재는 "
        f"절대 가져오지 말 것] {tone['example']}\n\n"
        f"[사용자가 선택한 컨셉]\n{concept['instruction']}\n"
        f"[컨셉 예시 — 관점과 초점만 참고하고, 예시의 구체적 사실·소재는 "
        f"절대 가져오지 말 것] {concept['example']}"
    )
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
    기존 build_unity_revision_prompt의 커스터마이징 확장판. example도 함께 준다 —
    윤문 단계에서도 목표 문체를 구체적 예문으로 다시 상기시켜야 챕터마다 흔들린
    어조를 일관되게 되돌릴 수 있다."""
    tone = TONE_OPTIONS[tone_key]
    concept = CONCEPT_OPTIONS[concept_key]

    system_prompt = (
        f"{UNITY_REVISION_SYSTEM_PROMPT}\n\n"
        f"[사용자가 선택한 말투]\n{tone['instruction']}\n"
        f"[말투 예시] {tone['example']}\n\n"
        f"[사용자가 선택한 컨셉]\n{concept['instruction']}\n"
        f"[컨셉 예시] {concept['example']}\n"
        f"윤문 시 이 말투와 컨셉의 일관성도 함께 확인하고 유지하세요."
    )
    user_prompt = f"[스타일 바이블]\n{style_bible}\n\n[전체 원고]\n{full_manuscript}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ── 11-6. 질문 리스트 → 커스터마이징 추천 (app/data/question_bank.py 연동) ────
#
#     각 고정 질문(QUESTION_BANK)에는 "질문 리스트.md" 원본이 붙여둔 suggested_tags
#     (예: "공간 및 장소 중심", "내밀한 고백체")가 있다 — 이 문항에 어떤 답을 하게
#     될지를 보고 사람이 미리 판단해 둔, 어울리는 말투/구성/컨셉에 대한 힌트다.
#     사용자가 실제로 답변을 남긴 문항들의 이 힌트를 모아 집계하면, 450가지
#     조합(말투10×구성5×컨셉9) 중 그 사람이 들려준 이야기 결과 자체에 가장 잘
#     맞는 조합을 추천할 수 있다 — select_customization 이전에 참고용으로 보여주는
#     용도이며, 사용자가 다른 조합을 고르는 것을 막지 않는다.
#
#     원본 표기가 TONE/STRUCTURE/CONCEPT_OPTIONS의 정식 키·name과 항상 정확히
#     일치하지는 않아(축약형, 유사어, 카테고리 간 쏠림 등) 아래 매핑을 사람이 직접
#     검토해 만들었다. 여기 없는 태그는 조용히 무시된다 — 추천은 힌트일 뿐이므로
#     매핑 누락이 기능을 깨뜨리면 안 된다.

_TAG_TO_OPTION: dict[str, tuple[str, str]] = {
    # 구성(Structure)
    "공간 및 장소 중심": ("structure", "geographical"),
    "공간 중심": ("structure", "geographical"),
    "사물 중심": ("structure", "thematic"),
    "결정적 에피소드": ("structure", "episodic"),
    "역순행적 구성": ("structure", "in_medias_res"),
    "연대기 구성": ("structure", "chronological"),
    "연대기 구성(에필로그)": ("structure", "chronological"),
    "테마별 구성": ("structure", "thematic"),
    "자유 구성": ("structure", "thematic"),
    "테마-사물": ("structure", "thematic"),
    "테마-가족": ("structure", "thematic"),
    "테마-인연": ("structure", "thematic"),
    "테마-꿈": ("structure", "thematic"),
    "테마-독립": ("structure", "thematic"),
    "테마-관계": ("structure", "thematic"),
    "테마-성장": ("structure", "thematic"),
    "테마-철학": ("structure", "thematic"),
    "테마-일과 삶": ("structure", "thematic"),
    "테마-직업": ("structure", "thematic"),
    "테마-취미": ("structure", "thematic"),
    "테마-일상": ("structure", "thematic"),
    "테마-배움": ("structure", "thematic"),
    "테마-가치관": ("structure", "thematic"),
    "테마-죽음": ("structure", "thematic"),
    "인연": ("structure", "thematic"),  # "테마-인연"의 표기 누락형(질문 리스트.md 원본 그대로)

    # 컨셉(Concept)
    "생애 전반 회고록": ("concept", "complete_memoir"),
    "가족사": ("concept", "family"),
    "가족사 및 양육기": ("concept", "family"),
    "비즈니스 & 리더십": ("concept", "business"),
    "멘토링 대담집": ("concept", "masterclass"),
    "3인칭 관찰자 평전": ("concept", "reporter"),
    "3인칭 평전": ("concept", "reporter"),
    "실패와 재기": ("concept", "resilience"),
    "실패와 재기의 기록": ("concept", "resilience"),
    "덕업일치": ("concept", "passion"),
    "덕업일치 및 취미 몰입기": ("concept", "passion"),
    "취미 몰입기": ("concept", "passion"),
    "가치관 사전": ("concept", "philosophical"),
    "가치관 및 철학 사전": ("concept", "philosophical"),
    "철학 사전": ("concept", "philosophical"),
    "특정 시기 집중": ("concept", "golden_era"),
    "특정 시기 집중 조명": ("concept", "golden_era"),
    "청춘의 낭만": ("concept", "golden_era"),

    # 말투(Tone)
    "소설적 서술체": ("tone", "literary"),
    "친근한 대화체": ("tone", "conversational"),
    "따뜻한 대화체": ("tone", "conversational"),
    "내밀한 고백체": ("tone", "confessional"),
    "담담한 평어체": ("tone", "plain"),
    "객관적 기록체": ("tone", "documentary"),
    "관조적 에세이": ("tone", "essay"),
    "관조적 에세이체": ("tone", "essay"),
    "유머러스한 풍자체": ("tone", "witty"),
    "가상의 인터뷰체": ("tone", "interview"),
    "대중 강연체": ("tone", "speech"),
    "편지체": ("tone", "letter"),
    "과거의 나에게 건네는 편지체": ("tone", "letter"),
}


def recommend_customization_keys(suggested_tags: list[str]) -> dict[str, list[str]]:
    """suggested_tags 원문 문자열 목록(여러 질문의 것을 그대로 합친 것)을 받아,
    카테고리(tone/structure/concept)별로 _TAG_TO_OPTION에 매핑되는 정식 옵션 키를
    등장 빈도 내림차순으로 정렬해 반환한다. 상위 몇 개를 쓸지는 호출부가 정한다
    (save_customization_selection이 카테고리당 1~2개를 요구하므로 보통 앞 1~2개만
    사용). 매핑에 없는 태그는 건너뛴다."""
    tallies: dict[str, Counter[str]] = {
        "tone": Counter(), "structure": Counter(), "concept": Counter(),
    }
    for tag in suggested_tags:
        mapped = _TAG_TO_OPTION.get(tag)
        if mapped is None:
            continue
        category, key = mapped
        tallies[category][key] += 1
    return {category: [key for key, _ in counter.most_common()] for category, counter in tallies.items()}


# ── 11-7. 콘텐츠 기반 커스터마이징 추천 (Phase 3, 스타일 바이블/사건 근거) ────
#
#     11-6의 태그 기반 추천은 "어떤 질문에 답했는가"만 본다 — 고정 질문 100개는
#     모든 유저가 같은 큐를 거치므로, 전부 답한 유저는 실제로 무슨 이야기를
#     했든 전원 같은 조합으로 수렴한다는 한계가 있다. 이 절은 그 대신 Phase 3에서
#     이미 생성되는 스타일 바이블(문체·가치관·감정 아크)과 중요도 순 사건 요약을
#     LLM에 그대로 보여주고, 실제 내용에 맞는 조합을 직접 고르게 한다 — 같은
#     100문항에 답했어도 사람마다 결과가 달라질 수 있다. consolidate_autobiography
#     (Phase 3) 안에서 스타일 바이블 생성과 함께 한 번 호출되고 결과가 style_bible.
#     recommended_customization에 저장된다(autobiography_service 참조).


def _describe_options(options: dict[str, dict[str, str]]) -> str:
    return "\n".join(f"- {key}: {value['name']} — {value['description']}" for key, value in options.items())


CUSTOMIZATION_RECOMMENDATION_SYSTEM_PROMPT_TEMPLATE = """\
당신은 자서전 집필 컨설턴트입니다. 아래 [스타일 바이블](화자의 문체·가치관·감정
아크 요약)과 [주요 사건 요약]을 읽고, 이 사람이 실제로 들려준 이야기에 가장 잘
어울리는 말투·구성·컨셉을 각각 1~2개씩 골라주세요.

판단 기준은 화자가 어떤 질문 문항에 답했는지가 아니라, 실제로 쓴 표현·소재·
정서입니다. 예를 들어 담담하고 사실 위주로 서술하는 사람에게는 감정을 과장하는
말투를 추천하지 마세요. 아래 나열된 키만 사용해야 합니다.

[말투 선택지]
{tone_descriptions}

[구성 선택지]
{structure_descriptions}

[컨셉 선택지]
{concept_descriptions}
"""

CUSTOMIZATION_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tones": {
            "type": "array",
            "items": {"type": "string", "enum": list(TONE_OPTIONS.keys())},
            "description": "가장 잘 어울리는 말투 키 1~2개, 잘 맞는 순서대로.",
        },
        "structures": {
            "type": "array",
            "items": {"type": "string", "enum": list(STRUCTURE_OPTIONS.keys())},
            "description": "가장 잘 어울리는 구성 키 1~2개, 잘 맞는 순서대로.",
        },
        "concepts": {
            "type": "array",
            "items": {"type": "string", "enum": list(CONCEPT_OPTIONS.keys())},
            "description": "가장 잘 어울리는 컨셉 키 1~2개, 잘 맞는 순서대로.",
        },
        "reasoning": {
            "type": "string",
            "description": "이 조합을 추천하는 근거를 1~2문장으로.",
        },
    },
    "required": ["tones", "structures", "concepts", "reasoning"],
    "additionalProperties": False,
}


def build_customization_recommendation_prompt(
    *, style_bible: str, event_summaries: str
) -> list[dict[str, str]]:
    system_prompt = CUSTOMIZATION_RECOMMENDATION_SYSTEM_PROMPT_TEMPLATE.format(
        tone_descriptions=_describe_options(TONE_OPTIONS),
        structure_descriptions=_describe_options(STRUCTURE_OPTIONS),
        concept_descriptions=_describe_options(CONCEPT_OPTIONS),
    )
    user_prompt = f"[스타일 바이블]\n{style_bible}\n\n[주요 사건 요약]\n{event_summaries}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

