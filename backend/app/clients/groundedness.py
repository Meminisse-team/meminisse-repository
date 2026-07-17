"""
Upstage Groundedness Check API 클라이언트 (model="groundedness-check").

챕터 근거검증(autobiography_service._run_groundedness_check)의 2차 게이트로 쓰인다 —
solar-pro3 판정(정교화 vs 날조 구분 담당)이 플래그한 문장을, RAG 검증 전용 소형
모델에게 한 번 더 물어 "grounded"로 확정되면 플래그를 철회한다(판정자의 오탐 제거).
solar.py와 동일한 Upstage 엔드포인트·클라이언트를 그대로 쓰고, 메시지 규약만 다르다:
user 역할에 근거 컨텍스트, assistant 역할에 검증할 문장을 실어 보내면 응답 본문이
"grounded" / "notGrounded" / "notSure" 한 단어로 온다(완료 토큰 ~3개라 매우 싸고
빠르다 — 문장 수만큼 병렬 호출해도 수 초 안에 끝난다).
"""

from __future__ import annotations

from app.clients.base import get_upstage_client

MODEL_NAME = "groundedness-check"

GROUNDED = "grounded"
NOT_GROUNDED = "notGrounded"
NOT_SURE = "notSure"


async def check(*, context: str, answer: str) -> str:
    """answer가 context에 근거하는지 판정한다. 반환값은 GROUNDED/NOT_GROUNDED/
    NOT_SURE 중 하나(모델이 규약 밖 문자열을 내면 그대로 반환 — 호출부는
    "GROUNDED와 정확히 일치할 때만 근거 있음"으로 보수적으로 해석해야 한다)."""
    client = get_upstage_client()
    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "user", "content": context},
            {"role": "assistant", "content": answer},
        ],
    )
    return (response.choices[0].message.content or "").strip()
