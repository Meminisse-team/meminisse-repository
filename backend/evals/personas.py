"""
P3(정량 평가체계) 1단계: 합성 페르소나 벤치마크용 페르소나 정의.

여기서 만드는 각 페르소나는 실제 인터뷰 파이프라인(app/services/interview_service.py +
app/services/event_extraction_service.py)을 그대로 통과시켜 대화 로그와 추출된 Event를
얻기 위한 "가짜 인터뷰이"다. GroundTruthEvent는 이후 단계(DeepEval 라벨추출 정확도 —
아직 미착수, backend/evals/README.md 참조)에서 "이 세션이 실제로 뽑아냈어야 할 정답"
으로 쓸 것을 염두에 두고, 필드명을 app/agents/prompts.py의 ALL_SLOTS 키(place, time,
event, emotion, values, companion, gratitude, regret, turning_point, pride, belief,
message)와 최대한 맞췄다 — Event 레코드 필드명과는 다르다(예: ground truth의 "time"은
Event.occurred_at_label에, "companion"은 Event.people에 대응). 매핑 상세는 README 참조.

지금은 5명 파일럿만 정의한다(발표 시간 제약으로 실제 API 호출 비용/시간을 먼저 검증한
뒤 30명으로 늘리기로 결정, 2026-07-12). 30명으로 늘릴 때는 이 리스트에 같은 형식으로
추가하면 되고, 다른 코드는 손댈 필요 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import LifePeriod

_LIFE_PERIOD_LABEL: dict[LifePeriod, str] = {
    LifePeriod.CHILDHOOD: "유년기",
    LifePeriod.YOUTH: "청년기",
    LifePeriod.ADULTHOOD: "장년기",
    LifePeriod.SENIOR: "노년기",
}


@dataclass
class GroundTruthEvent:
    """페르소나가 인터뷰 중 실제로 드러내야 할 사건 하나. 세션 하나 = 사건 하나로
    매핑한다(현재 interview_service의 slots_filled/followup_count가 세션 단위라
    한 세션 안에서 여러 사건을 다루는 흐름은 아직 오케스트레이션되어 있지 않다 —
    backend/docs/QUESTION_BANK_GUIDE.md 4절 참조). 그 제약에 맞춰 벤치마크도
    "세션당 사건 하나"로 설계했다."""

    life_period: LifePeriod
    age_at_time: int
    # 실제 인터뷰 시스템 프롬프트(build_interview_system_prompt)에 그대로 들어가는
    # "이번 세션이 다루는 생애주기" 라벨. 프로덕션 코드가 이 문자열을 자유 형식으로
    # 받으므로(app/schemas/sandbox.py의 예시 "유년기 (1950년대)") 여기서도 동일하게 구성한다.
    life_period_label: str
    place: str
    time: str  # 자유 형식 시기 표현(예: "1963년 봄"). Event.occurred_at_label에 대응.
    event: str  # 무슨 일이 있었는지 핵심 서술. Event.prose_paragraph/one_line_summary에 대응.
    emotion: str  # Event.emotion_tag에 대응.
    values: str  # Event.labels["values_reflected"]에 대응.
    companion: str  # 누구와 함께였는지(혼자였다면 "혼자"). Event.people에 대응.
    gratitude: str | None = None
    regret: str | None = None
    turning_point: str | None = None
    pride: str | None = None
    belief: str | None = None
    message: str | None = None
    # 페르소나가 첫 발화(세션 1턴)에서 "일부러" 언급하지 않을 필수 슬롯 키.
    # 전부 한 번에 말해버리면 꼬리 질문(FOLLOWUP_SYSTEM_PROMPT) 경로가 전혀
    # 실행되지 않아 파이프라인의 절반을 검증하지 못하므로, 최소 1개는 비워 둔다.
    withhold_on_first_turn: tuple[str, ...] = ()


@dataclass
class Persona:
    persona_id: str
    name: str
    birth_year: int
    hometown: str
    # 답변 톤·어투 지시(사투리, 문장 길이 등) — persona_agent.py가 시뮬레이션 프롬프트에
    # 그대로 넣어 페르소나별 발화 스타일을 다르게 만든다. 실제 시니어 사용자들의 어투
    # 다양성(사투리, 짧게 끊어 말하기 등)에 추출 파이프라인이 얼마나 강건한지 보기 위함.
    speech_style: str
    ground_truth_events: list[GroundTruthEvent] = field(default_factory=list)


PERSONAS: list[Persona] = [
    Persona(
        persona_id="p01_kim_soonja",
        name="김순자",
        birth_year=1955,
        hometown="부산",
        speech_style="부산 사투리를 섞어 쓰고, 문장을 짧게 끊어 말한다. 감정을 직접적으로 표현하기보다 에둘러 말하는 편.",
        ground_truth_events=[
            GroundTruthEvent(
                life_period=LifePeriod.CHILDHOOD,
                age_at_time=8,
                life_period_label="유년기 (1960년대)",
                place="부산 자갈치시장 근처 셋방",
                time="1963년 겨울",
                event="아버지의 포목 장사가 부도나 빚쟁이들을 피해 야반도주하듯 이사했다. 어머니가 그날부터 삯바느질로 다섯 식구를 먹여 살렸다.",
                emotion="불안하면서도 어머니에 대한 존경심",
                values="어떤 상황에서도 가족을 포기하지 않는다",
                companion="어머니와 남동생 둘",
                gratitude="밤새 바느질하던 어머니",
                regret="철없이 그때는 어머니 고생을 몰랐던 것",
                withhold_on_first_turn=("companion", "values"),
            )
        ],
    ),
    Persona(
        persona_id="p02_park_youngsoo",
        name="박영수",
        birth_year=1948,
        hometown="전남 목포",
        speech_style="차분하고 또박또박 말하며, 배움에 대한 이야기가 나오면 말이 길어진다.",
        ground_truth_events=[
            GroundTruthEvent(
                life_period=LifePeriod.YOUTH,
                age_at_time=21,
                life_period_label="청년기 (1960년대 말)",
                place="서울 구로공단 방직공장",
                time="1969년",
                event="목포에서 상경해 방직공장 야간조로 일하며, 새벽에 잠깐 눈을 붙이고 낮에는 검정고시 학원엘 다녔다. 손가락이 다 갈라지도록 실을 만졌다.",
                emotion="고달팠지만 배움에 대한 갈망이 컸다",
                values="배움에는 나이도 형편도 상관없다",
                companion="같은 기숙사 동료 두 명",
                pride="결국 검정고시에 붙었을 때",
                turning_point="야학 선생님이 '너는 계속 공부해야 할 사람'이라 해준 말",
                withhold_on_first_turn=("emotion", "pride"),
            )
        ],
    ),
    Persona(
        persona_id="p03_lee_junghee",
        name="이정희",
        birth_year=1952,
        hometown="대구",
        speech_style="조용하고 신중하게 말하며, 문장 끝을 흐리는 습관이 있다.",
        ground_truth_events=[
            GroundTruthEvent(
                life_period=LifePeriod.ADULTHOOD,
                age_at_time=31,
                life_period_label="장년기 (1980년대 초)",
                place="대구 친정집",
                time="1983년",
                event="첫아이를 어렵게 가졌다가 다섯 달 만에 유산했다. 반년 뒤 다시 아이가 생겼을 때는 매일 배를 쓸며 조마조마해했다.",
                emotion="깊은 상실감 뒤에 찾아온 조심스러운 기쁨",
                values="생명은 내 마음대로 되는 게 아니니 겸허해야 한다",
                companion="남편",
                gratitude="곁을 지켜준 남편과 친정어머니",
                regret="처음 유산했을 때 스스로를 탓했던 것",
                withhold_on_first_turn=("companion", "gratitude"),
            )
        ],
    ),
    Persona(
        persona_id="p04_choi_deoksoo",
        name="최덕수",
        birth_year=1945,
        hometown="강원 춘천",
        speech_style="군대 시절 이야기가 나오면 말투가 딱딱해지고 짧게 끊어 말한다.",
        ground_truth_events=[
            GroundTruthEvent(
                life_period=LifePeriod.ADULTHOOD,
                age_at_time=25,
                life_period_label="장년기 초입 (1970년)",
                place="베트남 퀴논 인근 정글",
                time="1970년 여름",
                event="파병 중 매복 작전에서 옆 전우가 부상당해 등에 업고 몇 시간을 걸어 후송했다. 그날 이후 밤에 소리에 예민해졌다.",
                emotion="극심한 공포와 전우에 대한 책임감",
                values="생명은 무엇과도 바꿀 수 없다",
                companion="같은 소대 전우들",
                pride="끝까지 전우를 업고 후송한 것",
                message="다시는 그런 전쟁이 없었으면 한다",
                withhold_on_first_turn=("companion", "message"),
            )
        ],
    ),
    Persona(
        persona_id="p05_han_malsoon",
        name="한말순",
        birth_year=1958,
        hometown="제주",
        speech_style="제주 방언 억양이 남아 있고, 바다 이야기를 할 때는 표현이 생생해진다.",
        ground_truth_events=[
            GroundTruthEvent(
                life_period=LifePeriod.YOUTH,
                age_at_time=19,
                life_period_label="청년기 (1970년대 말)",
                place="제주 서귀포 앞바다",
                time="1977년 여름",
                event="해녀였던 어머니를 따라 물질을 배우던 중 갑자기 인 큰 파도에 휩쓸릴 뻔했다. 어머니가 물속에서 끌어올려줘 겨우 살았다.",
                emotion="극심한 두려움과 이후 바다에 대한 경외감",
                values="자연 앞에서는 늘 겸손해야 한다",
                companion="어머니",
                turning_point="그 사고 이후로 바다를 대하는 마음가짐이 완전히 달라졌다",
                belief="바다는 주는 만큼 반드시 돌려받아야 하는 곳",
                withhold_on_first_turn=("values", "belief"),
            )
        ],
    ),
]


def life_period_korean_label(period: LifePeriod) -> str:
    return _LIFE_PERIOD_LABEL[period]
