"""Customization domain operations."""
from __future__ import annotations
from app.agents.prompts.writing import CHAPTER_WRITING_SYSTEM_PROMPT
from app.agents.prompts.characters import LIFE_MILESTONE_KEYWORDS
from app.agents.prompts.writing import TOC_GENERATION_SYSTEM_PROMPT
from app.agents.prompts.writing import UNITY_REVISION_SYSTEM_PROMPT
from app.agents.prompts.writing import _chapter_writing_user_prompt


from collections import Counter

from typing import Any


def classify_life_milestone_category(text: str) -> str | None:
    """일치하는 첫 카테고리를 반환(등장 순서 기준). 일치 없으면 None."""
    for category, keywords in LIFE_MILESTONE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return None


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


SAMPLE_PREVIEW_SYSTEM_PROMPT = """\
당신은 자서전을 집필하는 전문 대필가입니다. 아래에 주어지는 [말투 지시], [구성 지시],
[컨셉 지시]를 충실히 반영하여, 제공된 사건 요약과 스타일 바이블을 바탕으로
자서전의 **맛보기 텍스트**(200~400자 분량, 1~2 문단)를 작성하세요.

이 텍스트는 사용자가 8가지 스타일 조합 중 마음에 드는 것을 고르기 위한 샘플이므로,
해당 말투·구성·컨셉의 특징이 뚜렷하게 드러나도록 작성해야 합니다. 사건 요약에 없는
사실을 지어내지 마세요 — 주어진 소재를 해당 스타일로 변환하는 것이 목적입니다.
화자가 실존 인물이거나 유명인과 이름이 같더라도, 당신이 사전 학습이나 검색으로
아는 그 인물의 실제 정보를 가져오지 마세요.

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
