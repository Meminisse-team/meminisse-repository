"""Constants domain operations."""
from __future__ import annotations




import logging

import re















logger = logging.getLogger(__name__)


EVENT_MERGE_CANDIDATE_MAX_DISTANCE = 0.2


EVENT_MERGE_CANDIDATE_LIMIT = 3


MUST_INCLUDE_BONUS = 1000.0


MILESTONE_BONUS = 2.0


WEIGHT_LENGTH_Z = 1.0


WEIGHT_EMOTION_INTENSITY = 0.5


WEIGHT_MENTION_COUNT = 1.5


CHAPTER_RETRIEVAL_LIMIT = 20


MAX_REPAIR_PASSES = 2


PROOFREAD_MAX_LENGTH_DRIFT = 0.3


_OVERUSED_TERM_MIN_COUNT = 4


_OVERUSED_TERM_LIMIT = 5


_OVERUSED_TERM_STOPWORDS = frozenset({
    "나는", "내가", "나의", "그리고", "하지만", "그때", "그날", "있었다", "없었다",
    "했다", "것이", "우리는", "우리가", "지금도", "여전히", "함께", "속에서", "위에",
})


_KOREAN_WORD_PATTERN = re.compile(r"[가-힣]{2,}")


_SYNOPSIS_MARKDOWN_HEADER_PATTERN = re.compile(r"^#+\s*[^\n]*\n+")








_CITATION_TAG_PATTERN = re.compile(r"\[E(\d+)\]")


_PART_MARKER_PATTERN = re.compile(r"^=== ?PART\s+\d+:.*?===\s*$\n?", re.MULTILINE)


_CHAPTER_MARKER_PATTERN = re.compile(r"^<<<CHAPTER\s*(\d+)>>>\s*$", re.MULTILINE)


_CHAPTER_HEADER_PATTERN = re.compile(r"^\[\d+장\.[^\]]*\]\s*$\n?", re.MULTILINE)


_TOC_GENERATION_MAX_EVENTS = 150


_PART_TITLE_PREFIX_PATTERN = re.compile(
    r"^(?:Part|PART)\s*\d+\s*[:.\-–—]?\s*|^제?\d+부\s*[:.\-–—]?\s*"
)


_MIN_CHAPTERS_PER_PART = 3


_MIN_EVENTS_PER_CHAPTER_FALLBACK = 3


_ASSIGNMENT_YEAR_TOLERANCE = 8


_YEAR_IN_LABEL_PATTERN = re.compile(r"(1[89]\d{2}|20\d{2})")


_PLACE_ALIASES: dict[str, str] = {
    "서울시": "서울", "서울특별시": "서울",
    "부산시": "부산", "부산광역시": "부산",
    "대구시": "대구", "대구광역시": "대구",
    "인천시": "인천", "인천광역시": "인천",
    "광주시": "광주", "광주광역시": "광주",
    "대전시": "대전", "대전광역시": "대전",
    "울산시": "울산", "울산광역시": "울산",
    "세종시": "세종", "세종특별자치시": "세종",
}


_PLACE_PARTICLES = ("에서부터", "으로부터", "에서는", "에서", "으로는", "부터", "까지", "에는", "으로", "에", "로")


_KOREAN_AGE_TENS: dict[str, int] = {
    "스물": 20, "서른": 30, "마흔": 40, "쉰": 50,
    "예순": 60, "일흔": 70, "여든": 80, "아흔": 90,
}


_KOREAN_AGE_UNITS: dict[str, int] = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
}


_DIGIT_AGE_PATTERN = re.compile(r"(\d{1,3})\s*(?:살|세)")


_KOREAN_AGE_WORD_PATTERN = re.compile(
    "(" + "|".join(sorted(_KOREAN_AGE_TENS, key=len, reverse=True)) + ")"
    "(" + "|".join(sorted(_KOREAN_AGE_UNITS, key=len, reverse=True)) + ")?"
    r"\s*(?:살|세)"
)


_FINALIZE_BATCH_MAX_CHAPTERS = 5


_FINALIZE_BATCH_MAX_TOKENS = 32000


_FINALIZE_BATCH_TIMEOUT_SECONDS = 180.0



from app.autobiography.policy import MIN_COMPLETED_SESSIONS_FOR_AUTOBIOGRAPHY, RECOMMENDED_COMPLETED_SESSIONS, AUTOBIOGRAPHY_PROGRESS_TOTAL
