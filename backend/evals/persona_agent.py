"""
합성 페르소나가 실제 사람처럼 인터뷰에 답하도록 Solar를 이용해 발화를 시뮬레이션한다.

인터뷰 에이전트(app/agents/prompts.py의 INTERVIEW_PERSONA_SYSTEM_PROMPT)와 대칭되는
"인터뷰이 역"을 맡기는 것뿐이라, 같은 solar.chat_completion을 그대로 재사용한다 — 실제
프로덕션 파이프라인에 새 의존성을 추가하지 않는다(이 모듈은 backend/evals/ 밑에만
있고 app/ 코드는 이 모듈을 참조하지 않는다).
"""

from __future__ import annotations

from app.clients import solar
from evals.personas import GroundTruthEvent, Persona

_MAX_TURNS_PER_EVENT = 3  # 페르소나 첫 발화 1회 + 꼬리 질문 응답 최대 2회(MAX_FOLLOWUP_PER_EVENT와 동일 예산)

_OPENING_LINE = "오늘은 어떤 기억을 함께 떠올려볼까요? 편하게 말씀해주세요."


def _persona_system_prompt(persona: Persona, gt: GroundTruthEvent) -> str:
    withheld = ", ".join(gt.withhold_on_first_turn) if gt.withhold_on_first_turn else "없음"
    return f"""\
당신은 자서전 인터뷰에 응하는 실제 시니어 화자 "{persona.name}"({persona.birth_year}년생,
{persona.hometown} 출신)를 연기하는 롤플레이 배우입니다. AI 어시스턴트가 아니라 이
사람 본인이 되어, 아래 사건을 실제로 겪은 사람처럼 1인칭으로 자연스럽게 대답하세요.

[말투 지시]
{persona.speech_style}

[이번에 다룰 사건 — 당신의 실제 기억]
- 시기: {gt.time} ({gt.age_at_time}세 무렵)
- 장소: {gt.place}
- 있었던 일: {gt.event}
- 그때 감정: {gt.emotion}
- 이 사건에 담긴 가치관: {gt.values}
- 함께 있었던 사람: {gt.companion}

[답변 규칙]
- 인터뷰어가 묻는 것에 자연스럽게 답하되, 한 번에 모든 것을 다 말하지 마세요 —
  실제 대화처럼 조금씩 풀어놓으세요.
- 첫 발화에서는 특히 다음 정보는 아직 언급하지 마세요(인터뷰어가 물어보면 그때
  답하세요): {withheld}
- 2~4문장 정도의 짧은 구어체 답변만 하세요. 해설하거나 사건을 요약하지 말고,
  그 순간을 실제로 겪은 사람처럼 구체적인 디테일을 섞어 말하세요.
- 절대로 "AI", "언어 모델", "롤플레이" 같은 말을 하지 마세요. 끝까지 이 사람으로만 답하세요.
"""


async def generate_persona_turn(
    *,
    persona: Persona,
    gt: GroundTruthEvent,
    transcript_so_far: list[dict[str, str]],
) -> str:
    """지금까지의 대화(transcript_so_far, role은 인터뷰 관점 그대로 user/assistant)를
    페르소나 시점으로 뒤집어(인터뷰어의 assistant 발화 → user, 페르소나의 이전 user
    발화 → assistant) Solar에 넘긴다. 이렇게 하면 이 함수의 반환값(다음 assistant
    메시지)이 곧 페르소나가 이번에 할 발화가 된다."""
    flipped = [
        {"role": "user" if turn["role"] == "assistant" else "assistant", "content": turn["content"]}
        for turn in transcript_so_far
    ]
    if not flipped:
        # 첫 턴: 실제 서비스에서도 이 안내 문구는 DB에 저장되지 않는 순수 프론트엔드
        # placeholder다(frontend/src/components/chat/ChatOverlay.tsx의 OPENING_LINE
        # 참조) — 그래서 여기서도 대화 로그가 아니라 이번 한 번의 유저 메시지로만 넣는다.
        flipped = [{"role": "user", "content": _OPENING_LINE}]

    messages = [{"role": "system", "content": _persona_system_prompt(persona, gt)}, *flipped]
    response = await solar.chat_completion(messages, reasoning_effort="low", max_tokens=200)
    return (response.choices[0].message.content or "").strip()
