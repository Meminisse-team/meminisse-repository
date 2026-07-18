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


async def check(*, context: str, answer: str) -> str:
    """answer가 context에 근거하는지 판정한다. 반환값은 GROUNDED/NOT_GROUNDED/
    NOT_SURE 중 하나(모델이 규약 밖 문자열을 내면 그대로 반환 — 호출부는
    "GROUNDED와 정확히 일치할 때만 근거 있음"으로 보수적으로 해석해야 한다)."""
    client = get_upstage_client()
    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"[근거]\n{context}\n\n[검증할 문장]\n{answer}"},
        ],
        max_tokens=20,
    )
    return (response.choices[0].message.content or "").strip()
