"""
챕터 근거검증의 2차 게이트 클라이언트 — solar-pro3 판정(정교화 vs 날조 구분 담당)이
플래그한 문장을 한 번 더 확인해 "grounded"로 확정되면 플래그를 철회한다(판정자의
오탐 제거).

원래는 Upstage 전용 소형 모델(model="groundedness-check")을 썼는데, 2026-07-18
실측 결과 이 모델이 Upstage에서 완전히 폐기됐음을 확인했다 — GET /models 목록에
없고(solar-pro3/pro2/mini, syn-pro만 존재), 호출하면 400("invalid or no longer
supported")이 돌아온다. 호출부의 보수적 예외 처리(실패 시 플래그 유지) 탓에
크래시 없이 조용히 무력화된 채 방치돼, 배포 이후 단 한 건의 플래그도 철회되지
못했다(호킹 계정 챕터 10개 전부 dismissed_by_groundedness_api == 0).

대체: 같은 판정을 solar-mini(현존 최소·최저가 모델)에 이분 판정 프롬프트로
요청한다. 인터페이스(check(context, answer) -> str)와 반환 규약(grounded /
notGrounded / notSure)은 그대로 유지해 호출부는 무변경이다. 완료 토큰이 한
단어라 여전히 싸고 빠르며, 문장 수만큼 병렬 호출해도 수 초 안에 끝난다.

**solar-mini를 solar-pro3로 "업그레이드"하고 싶어질 수 있다 — 하지 말 것.**
"같은 값이면 더 큰 모델이 낫지 않냐"는 질문이 실제로 나왔고, evals/
groundedness_gate_accuracy.py로 실측했다(2026-07-18, n=20쌍: 정당한 문학적
정교화 10건 + 날조 10건). 결과는 반대였다 — solar-mini는 위험한 방향의 오판
(날조를 grounded로 오판, 최종 원고에 환각이 새는 방향) 0/10인데 solar-pro3는
2/10이었다(evals/README.md 5절). 챕터 본문 집필과 1차 판정(_run_groundedness_
check)이 이미 solar-pro3이므로, 2차 게이트까지 같은 계열로 통일하면 같은 모델이
자기 출력을 검증하는 자기선호 편향(기획안 6절이 벤치마크 판정 모델 선정에서
경계한 것과 같은 문제, Zheng et al. LLM-as-Judge 참조)이 실제로 관측된 것으로
보인다. 모델을 바꾸려면 반드시 evals/groundedness_gate_accuracy.py를 먼저
재실행해 실측으로 뒷받침할 것 — 크기가 크다고 이 자리에 더 적합하다는 보장은
없다.
"""

from __future__ import annotations

from app.clients.base import get_upstage_client

MODEL_NAME = "solar-mini"

GROUNDED = "grounded"
NOT_GROUNDED = "notGrounded"
NOT_SURE = "notSure"

# 폐기된 전용 모델의 응답 규약(grounded/notGrounded/notSure 한 단어)을 프롬프트로
# 재현한다. 호출부는 "GROUNDED와 정확히 일치할 때만 근거 있음"으로 보수적으로
# 해석하므로, 모델이 규약을 어기고 다른 문자열을 내면 플래그 유지로 안전하게
# 처리된다.
_SYSTEM_PROMPT = """\
당신은 RAG 근거 검증기입니다. [근거]와 [검증할 문장]이 주어집니다.
문장이 근거에 담긴 내용으로 뒷받침되면 grounded, 근거와 모순되거나 근거에
없는 새로운 사실을 주장하면 notGrounded, 판단할 수 없으면 notSure를 출력하세요.
반드시 grounded, notGrounded, notSure 중 정확히 한 단어만 출력하고, 설명이나
다른 텍스트를 붙이지 마세요.
"""


async def check(*, context: str, answer: str, model: str = MODEL_NAME) -> str:
    """answer가 context에 근거하는지 판정한다. 반환값은 GROUNDED/NOT_GROUNDED/
    NOT_SURE 중 하나(모델이 규약 밖 문자열을 내면 그대로 반환 — 호출부는
    "GROUNDED와 정확히 일치할 때만 근거 있음"으로 보수적으로 해석해야 한다).

    model: 프로덕션 호출부는 인자를 생략해 MODEL_NAME(solar-mini)을 쓴다.
    evals/groundedness_gate_accuracy.py가 solar-pro3 등 다른 모델과의 정확도
    비교를 위해서만 명시적으로 오버라이드한다."""
    client = get_upstage_client()
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"[근거]\n{context}\n\n[검증할 문장]\n{answer}"},
        ],
        max_tokens=20,
    )
    return (response.choices[0].message.content or "").strip()
