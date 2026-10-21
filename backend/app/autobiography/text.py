"""Text domain operations."""
from __future__ import annotations








from datetime import datetime, timezone












def _strip_prompt_section_echo(text: str, *, body_marker: str) -> str:
    """검수/확장 모델이 user 메시지의 섹션 헤더("[챕터 본문]" 등)를 출력에 그대로
    되돌려보내는 에코 방어(2026-07-18 라이브 실측 — 7장 본문에 "[완화 대상 반복
    표현 …]" 블록이 통째로 저장됨). body_marker가 출력에 남아 있으면 그 뒤부터가
    실제 본문이다 — 마커 앞의 모든 에코(지시 블록 포함)를 버린다."""
    if body_marker in text:
        text = text.split(body_marker, 1)[1]
    return text.strip()


def _strip_synopsis_markdown_header(text: str) -> str:
    from app.autobiography.constants import _SYNOPSIS_MARKDOWN_HEADER_PATTERN
    return _SYNOPSIS_MARKDOWN_HEADER_PATTERN.sub("", text.strip(), count=1).strip()


def _count_overused_terms(content: str) -> list[str]:
    """본문에서 _OVERUSED_TERM_MIN_COUNT회 이상 등장한 한글 단어(불용어 제외)를
    빈도 내림차순 상위 _OVERUSED_TERM_LIMIT개까지 반환한다. 조사·어미가 붙은
    변형까지 묶는 형태소 분석은 하지 않는다 — 검수 프롬프트에 지목할 후보만
    뽑으면 되는 저비용 휴리스틱이라 완벽할 필요가 없다."""
    from app.autobiography.constants import _KOREAN_WORD_PATTERN
    from app.autobiography.constants import _OVERUSED_TERM_LIMIT
    from app.autobiography.constants import _OVERUSED_TERM_MIN_COUNT
    from app.autobiography.constants import _OVERUSED_TERM_STOPWORDS
    counts: dict[str, int] = {}
    for word in _KOREAN_WORD_PATTERN.findall(content):
        if word in _OVERUSED_TERM_STOPWORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
    frequent = [
        (word, count) for word, count in counts.items() if count >= _OVERUSED_TERM_MIN_COUNT
    ]
    frequent.sort(key=lambda item: (-item[1], item[0]))
    return [word for word, _ in frequent[:_OVERUSED_TERM_LIMIT]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
