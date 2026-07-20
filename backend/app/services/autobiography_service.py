"""
Phase 3(이벤트 병합·중요도 산정·스타일 바이블)과 Phase 4(동적 목차·하향식 집필·
팩트체크·근거검증·제3자 위해성 분류) 오케스트레이션.

Phase 3/4는 무거운 LLM 호출이 여러 번 이어지는 연산이므로(기획안 4절: "세션 종료 후
이벤트 추출, 최종 집필 ... Celery+Redis 독립 워커에서 처리"), 이 모듈의 최상위 진입점
(consolidate_autobiography, write_chapter, finalize_manuscript)은 app/workers/tasks.py의
Celery 태스크를 통해 호출되어야 API 서버 타임아웃을 피할 수 있다. 목차 생성/선택처럼
LLM 호출 1회로 끝나는 가벼운 단계는 API 요청 경로에서 직접 await해도 무방하다.

DB 접근은 전부 app.gateways를 통한다 — 이 파일은 SQLAlchemy를 알지 못한다. 공개
진입점(위 5개 + get_or_create_autobiography 등 router가 직접 부르는 함수)만 각자
gateways.commit()을 한 번 호출한다; 비공개(`_` 접두) 헬퍼는 커밋하지 않는다.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
import statistics
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.agents import prompts
from app.clients import embeddings as embeddings_client
from app.clients import groundedness as groundedness_client
from app.clients import llm_router
from app.data.question_bank import QUESTION_BANK_BY_SEQUENCE
from app.gateways.dto import (
    AutobiographyRecord,
    AutobiographyStatusRecord,
    ChapterDraftCreateData,
    ChapterDraftRecord,
    ChapterDraftWriteResult,
    ChapterStatusRecord,
    EventImportanceUpdate,
    EventRecord,
)
from app.gateways.factory import Gateways
from app.models.enums import AssetType, AutobiographyStatus, DraftStatus, LifeMilestoneCategory, UserStage
from app.services import character_service

logger = logging.getLogger(__name__)

# Phase 3 이벤트 병합: 이 값보다 코사인 거리가 가까운(=유사한) 쌍만 LLM 병합 판정에
# 회부한다. Upstage 임베딩 실측 전 잠정값 — 실제 임베딩으로 캘리브레이션 필요.
EVENT_MERGE_CANDIDATE_MAX_DISTANCE = 0.2
EVENT_MERGE_CANDIDATE_LIMIT = 3

# Phase 3 중요도 스코어링 가중치. 사용자 명시 지정('꼭 넣기')은 다른 신호를 압도하는
# 고정 우선순위여야 하므로(기획안: "최우선 고정") 큰 상수로 그 외 신호와 분리한다.
MUST_INCLUDE_BONUS = 1000.0
MILESTONE_BONUS = 2.0
WEIGHT_LENGTH_Z = 1.0
WEIGHT_EMOTION_INTENSITY = 0.5
WEIGHT_MENTION_COUNT = 1.5

# Phase 4 하이브리드 검색(의미 검색 + 키워드 정확 매칭)에서 챕터당 소환할 이벤트 상한.
# 예전에는 10이었는데, 챕터 원재료가 너무 빈약해 챕터가 지나치게 짧게(1,600~2,400자)
# 나오는 원인 중 하나였다. _run_groundedness_check를 Solar LLM 판정으로 옮긴 뒤에는
# (아래 함수 주석 참조) 사건 개수가 늘어도 로컬 NLI 연산량과 무관해져 이 값을 계속
# 늘려도 안전하다.
CHAPTER_RETRIEVAL_LIMIT = 20

# 팩트체크/근거검증 플래그가 남아 있는 동안 외과적 수리(_repair_chapter_content)를
# 반복하는 최대 횟수. 수리는 전체 재집필이 아니라 플래그된 문장만 고치는 국소
# 작업이라 1회로 대부분 수렴하지만, 수리 결과가 다시 걸리는 경우를 위해 한 번 더
# 기회를 준다. 그 이상은 비용 대비 개선이 없어 잔여 플래그를 리포트에 남기고 끝낸다.
MAX_REPAIR_PASSES = 2

# 검수(교열) 패스 결과가 원본 대비 이 비율을 넘게 길이가 변하면 교열 범위를
# 벗어나 개고를 한 것으로 보고 원본을 유지한다(폭주 방어).
PROOFREAD_MAX_LENGTH_DRIFT = 0.3

# 검수 패스에 "완화 대상 반복 표현"으로 지목할 기준 — 본문에서 이 횟수 이상
# 등장한 한글 단어를 결정론적으로 세어 프롬프트에 넘긴다("같은 표현 반복 완화"
# 일반 지시만으로는 '덤' 4회 남발이 그대로 남던 실측 대응, 2026-07-18).
_OVERUSED_TERM_MIN_COUNT = 4
_OVERUSED_TERM_LIMIT = 5
# 반복이 자연스러운 고빈도 일반어 — 반복 표현 후보에서 제외한다.
_OVERUSED_TERM_STOPWORDS = frozenset({
    "나는", "내가", "나의", "그리고", "하지만", "그때", "그날", "있었다", "없었다",
    "했다", "것이", "우리는", "우리가", "지금도", "여전히", "함께", "속에서", "위에",
})
_KOREAN_WORD_PATTERN = re.compile(r"[가-힣]{2,}")


def _strip_prompt_section_echo(text: str, *, body_marker: str) -> str:
    """검수/확장 모델이 user 메시지의 섹션 헤더("[챕터 본문]" 등)를 출력에 그대로
    되돌려보내는 에코 방어(2026-07-18 라이브 실측 — 7장 본문에 "[완화 대상 반복
    표현 …]" 블록이 통째로 저장됨). body_marker가 출력에 남아 있으면 그 뒤부터가
    실제 본문이다 — 마커 앞의 모든 에코(지시 블록 포함)를 버린다."""
    if body_marker in text:
        text = text.split(body_marker, 1)[1]
    return text.strip()


# 책 시놉시스 응답이 "# 시놉시스: 『제목』" 같은 마크다운 제목 줄로 시작하는 경우를
# 잡는다 — Claude 계열 모델이 프롬프트에 "제목을 붙이지 말라"는 명시적 지시가
# 없으면 관성적으로 헤더를 앞에 붙이는 경향이 실사용 중 확인됐다(2026-07-20,
# PDF 소개 페이지에서 제목·본문이 한 문단으로 뭉쳐 보이는 사고로 발견). 프롬프트
# 자체도 이 지시를 명시하도록 고쳤지만(BOOK_SYNOPSIS_SYSTEM_PROMPT), 지시를
# 어기는 경우에 대비한 코드 레벨 백스톱이다 — _strip_prompt_section_echo와 같은
# 발상.
_SYNOPSIS_MARKDOWN_HEADER_PATTERN = re.compile(r"^#+\s*[^\n]*\n+")


def _strip_synopsis_markdown_header(text: str) -> str:
    return _SYNOPSIS_MARKDOWN_HEADER_PATTERN.sub("", text.strip(), count=1).strip()


def _count_overused_terms(content: str) -> list[str]:
    """본문에서 _OVERUSED_TERM_MIN_COUNT회 이상 등장한 한글 단어(불용어 제외)를
    빈도 내림차순 상위 _OVERUSED_TERM_LIMIT개까지 반환한다. 조사·어미가 붙은
    변형까지 묶는 형태소 분석은 하지 않는다 — 검수 프롬프트에 지목할 후보만
    뽑으면 되는 저비용 휴리스틱이라 완벽할 필요가 없다."""
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

# 자서전 집필 시작 가능 기준 — 완료된 세션(재조립 산문)이 이 개수 이상 쌓여야
# 재료가 충분하다고 보고 "자서전 집필"을 열어준다(2026-07-17 제품 결정). 130은
# 고정 질문 100개 + 사진/에피소드 세션을 더한 대략적인 전체 목표치로, 이 값
# 자체를 강제하는 별도 상한은 없다 — 진행률 바의 분모로만 쓰인다.
MIN_COMPLETED_SESSIONS_FOR_AUTOBIOGRAPHY = 50
RECOMMENDED_COMPLETED_SESSIONS = 80
AUTOBIOGRAPHY_PROGRESS_TOTAL = 130

# 집필 프롬프트의 근거 태그 규약([E1], [E2]...) — prompts._numbered_events_block과
# 같은 번호 체계. 조판 전에 본문에서 회수·제거된다(_strip_citation_tags).
_CITATION_TAG_PATTERN = re.compile(r"\[E(\d+)\]")

# finalize_manuscript가 윤문 입력에 심어두는 Part 경계 안내 마커
# ("=== PART N: 제목 ===")를 최종 출력에서 방어적으로 제거하기 위한 패턴 —
# UNITY_REVISION_SYSTEM_PROMPT가 이 마커를 최종 출력에 남기지 말라고 명시하지만,
# LLM이 지시를 어기는 경우를 대비한 안전망이다.
_PART_MARKER_PATTERN = re.compile(r"^=== ?PART\s+\d+:.*?===\s*$\n?", re.MULTILINE)

# 최종 통일성 윤문의 챕터 경계 마커/헤더(UNITY_REVISION_SYSTEM_PROMPT 출력 규약과
# 쌍). finalize_manuscript가 윤문 결과를 챕터별로 되나누는 데 쓴다(2026-07-18).
_CHAPTER_MARKER_PATTERN = re.compile(r"^<<<CHAPTER\s*(\d+)>>>\s*$", re.MULTILINE)
_CHAPTER_HEADER_PATTERN = re.compile(r"^\[\d+장\.[^\]]*\]\s*$\n?", re.MULTILINE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create_autobiography(gateways: Gateways, user_id: uuid.UUID) -> AutobiographyRecord:
    """"자서전 집필"이 이어서 작업할 자서전을 찾는다 — 미완성(final_content
    없음) 버전이 있으면 그중 최신 것을 이어서 쓰고, 전부 완성됐거나 하나도
    없으면 새 버전을 만든다(2026-07-17, migration 015로 유저당 여러 버전이
    가능해지면서 "자서전 집필"에 다시 들어가면 자동으로 새 버전이 시작되도록
    바뀜 — 별도 "새 버전 시작" 버튼 없이도 이전 버전은 "나의 책장"에 그대로
    남는다)."""
    autobiography = await gateways.autobiographies.get_latest_unfinished_by_user(user_id)
    if autobiography is not None:
        return autobiography
    autobiography = await gateways.autobiographies.create(user_id)
    await gateways.commit()
    return autobiography


async def list_finished_autobiographies(
    gateways: Gateways, user_id: uuid.UUID
) -> list[AutobiographyRecord]:
    """"나의 책장" 전용 — 이 유저가 완성한(final_content 有) 자서전 전체."""
    return await gateways.autobiographies.list_finished_by_user(user_id)


async def get_autobiography_by_id(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    autobiography = await gateways.autobiographies.get_by_id(autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {autobiography_id} not found")
    return autobiography


async def list_chapter_drafts(gateways: Gateways, autobiography_id: uuid.UUID) -> list[ChapterDraftRecord]:
    return await gateways.chapters.list_by_autobiography(autobiography_id)


async def get_polling_status(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> tuple[AutobiographyStatusRecord, list[ChapterStatusRecord]] | None:
    """자서전 집필 화면의 폴링 전용 경량 조회(2026-07-19) — final_content/챕터
    본문 같은 무거운 필드를 뺀 상태만 반환한다(app/gateways/dto.py의
    AutobiographyStatusRecord/ChapterStatusRecord 참조). 존재하지 않으면
    None(호출부가 404로 변환)."""
    autobiography_status = await gateways.autobiographies.get_status_by_id(autobiography_id)
    if autobiography_status is None:
        return None
    chapters_status = await gateways.chapters.list_status_by_autobiography(autobiography_id)
    return autobiography_status, chapters_status


async def get_chapter_draft(gateways: Gateways, chapter_draft_id: uuid.UUID) -> ChapterDraftRecord | None:
    return await gateways.chapters.get(chapter_draft_id)


class InvalidPhotoPlacementError(Exception):
    """수록 사진 배치 지정이 유효하지 않다 — 본인 소유가 아니거나 이미지가 아닌
    미디어를 가리키거나, 존재하지 않는 chapter_index를 가리키는 경우. 라우터가
    400으로 매핑한다(app/api/v1/autobiographies.py)."""


async def set_photo_placements(
    gateways: Gateways, autobiography: AutobiographyRecord, placements: list[dict]
) -> AutobiographyRecord:
    """PDF 조판 직전, 사용자가 고른 수록 사진과 배치(고정 슬롯)를 저장한다.

    placements의 각 항목은 스키마(PhotoPlacementItem)가 이미 형태를 검증한
    JSON 직렬화 가능 dict다 — 여기서는 참조 무결성만 본다: media_asset이 실재하고
    본인 소유의 이미지인지, chapter_index가 실제 챕터를 가리키는지. 빈 배열도
    유효하다("수록 사진 없음"으로 확정). 조판은 여기 저장된 지정만 반영하며,
    저장한 적이 없으면(None) 사진 없이 조판된다 — 자동 선택은 하지 않는다."""
    chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    valid_chapter_indexes = {chapter.chapter_index for chapter in chapters}

    for placement in placements:
        media_asset_id = uuid.UUID(str(placement["media_asset_id"]))
        media_asset = await gateways.media_assets.get_by_id(media_asset_id)
        if (
            media_asset is None
            or media_asset.user_id != autobiography.user_id
            or media_asset.asset_type != AssetType.IMAGE
        ):
            raise InvalidPhotoPlacementError(f"사용할 수 없는 사진입니다: {media_asset_id}")
        if placement["chapter_index"] not in valid_chapter_indexes:
            raise InvalidPhotoPlacementError(
                f"존재하지 않는 챕터입니다: {placement['chapter_index']}장"
            )

    updated = await gateways.autobiographies.update(
        autobiography.id, photo_placements=placements
    )
    await gateways.commit()
    return updated


# --------------------------------------------------------------------------- #
# 자서전 커스터마이징 — 말투·구성·컨셉 선택 / 샘플 미리보기 / 확정             #
# --------------------------------------------------------------------------- #


def _get_confirmed_customization(autobiography: AutobiographyRecord) -> dict | None:
    """확정된 커스터마이징 설정을 반환. 없으면 None."""
    style_bible = autobiography.style_bible or {}
    customization = style_bible.get("customization", {})
    return customization.get("confirmed") if customization.get("confirmed") else None


async def save_customization_selection(
    gateways: Gateways,
    autobiography_id: uuid.UUID,
    *,
    tones: list[str],
    structures: list[str],
    concepts: list[str],
) -> AutobiographyRecord:
    """사용자가 각 카테고리에서 2개씩 선택한 결과를 style_bible.customization에 저장한다."""
    # 유효성 검증
    for tone in tones:
        if tone not in prompts.TONE_OPTIONS:
            raise ValueError(f"유효하지 않은 말투 키: {tone}")
    for structure in structures:
        if structure not in prompts.STRUCTURE_OPTIONS:
            raise ValueError(f"유효하지 않은 구성 키: {structure}")
    for concept in concepts:
        if concept not in prompts.CONCEPT_OPTIONS:
            raise ValueError(f"유효하지 않은 컨셉 키: {concept}")
    if not (1 <= len(tones) <= 2):
        raise ValueError(f"말투는 1~2개를 선택해야 합니다 (현재 {len(tones)}개)")
    if not (1 <= len(structures) <= 2):
        raise ValueError(f"구성은 1~2개를 선택해야 합니다 (현재 {len(structures)}개)")
    if not (1 <= len(concepts) <= 2):
        raise ValueError(f"컨셉은 1~2개를 선택해야 합니다 (현재 {len(concepts)}개)")

    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = dict(autobiography.style_bible or {})
    style_bible["customization"] = {
        "selected_at": _now_iso(),
        "tones": tones,
        "structures": structures,
        "concepts": concepts,
        "confirmed": None,
        "previews": None,
    }
    autobiography = await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()
    return autobiography


async def _recommend_customization_from_tags(
    gateways: Gateways, autobiography: AutobiographyRecord
) -> dict[str, list[str]]:
    """태그 기반 즉석 힌트(Phase 3 이전에도 동작) — 이 유저가 실제로 답변을 남긴
    고정 질문들의 suggested_tags(app/data/question_bank.py)를 모아 말투·구성·컨셉
    각 카테고리별로 어울리는 옵션 키를 빈도순으로 추천한다. 고정 질문 100개는
    모든 유저가 동일한 큐를 거치므로, 전부 답한 유저는 실제 이야기 내용과
    무관하게 전원 같은 조합으로 수렴한다는 한계가 있다 — 그래서 Phase 3가 끝나
    콘텐츠 기반 추천(_generate_content_based_customization_recommendation)이
    준비되면 get_customization_recommendations는 그쪽을 우선한다."""
    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    session_ids = {event.session_id for event in events if event.session_id is not None}
    if not session_ids:
        return {"tones": [], "structures": [], "concepts": []}

    sessions = await gateways.sessions.list_by_user(autobiography.user_id)
    question_ids = {
        session.question_id
        for session in sessions
        if session.id in session_ids and session.question_id is not None
    }

    suggested_tags: list[str] = []
    for question_id in question_ids:
        question = await gateways.questions.get_by_id(question_id)
        if question is None:
            continue
        entry = QUESTION_BANK_BY_SEQUENCE.get(question.sequence_order)
        if entry is not None:
            suggested_tags.extend(entry["suggested_tags"])

    ranked = prompts.recommend_customization_keys(suggested_tags)
    # save_customization_selection이 카테고리당 1~2개를 요구하므로 상위 2개만 추천한다.
    return {
        "tones": ranked["tone"][:2],
        "structures": ranked["structure"][:2],
        "concepts": ranked["concept"][:2],
    }


async def _generate_content_based_customization_recommendation(
    gateways: Gateways, user_id: uuid.UUID, style_bible_text: str
) -> dict | None:
    """Phase 3(consolidate_autobiography)에서 스타일 바이블 생성 직후 한 번 호출된다.
    태그 기반 추천(_recommend_customization_from_tags)과 달리 "어떤 질문에
    답했는가"가 아니라 실제로 화자가 쓴 문체·소재·정서(스타일 바이블 + 중요도 순
    사건 요약)를 LLM에 보여주고 직접 판단하게 한다 — 그래서 같은 100문항에 답했어도
    사람마다 다른 추천이 나올 수 있다. 이벤트가 없으면(사건 추출 전) None을 반환해
    호출부가 태그 기반으로 폴백하게 한다."""
    events = await gateways.events.list_unmerged_verified(user_id)
    if not style_bible_text or not events:
        return None

    event_summaries = "\n".join(
        f"- [중요도 {event.importance_score}] {event.one_line_summary} "
        f"(시기: {event.occurred_at_label or '미상'}, 감정: {event.emotion_tag or '미상'})"
        for event in events[:15]
    )
    result = await llm_router.structured_completion(
        prompts.build_customization_recommendation_prompt(
            style_bible=style_bible_text, event_summaries=event_summaries
        ),
        schema_name="customization_recommendation",
        json_schema=prompts.CUSTOMIZATION_RECOMMENDATION_SCHEMA,
        reasoning_effort="medium",
    )
    return {
        "tones": result.get("tones", [])[:2],
        "structures": result.get("structures", [])[:2],
        "concepts": result.get("concepts", [])[:2],
        "reasoning": result.get("reasoning", ""),
    }


async def get_customization_recommendations(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> dict:
    """말투·구성·컨셉 추천을 반환한다(하이브리드). Phase 3가 끝나 콘텐츠 기반
    추천(style_bible.recommended_customization)이 이미 있으면 그것을 그대로
    쓰고("content_based"), 아직 없으면(Phase 3 이전, 또는 이벤트가 없어 콘텐츠
    기반 추천 자체가 생성되지 않은 경우) 태그 기반 즉석 힌트로 대체한다
    ("tag_based"). 어느 쪽이든 참고용일 뿐 강제가 아니며, save_customization_
    selection 단계에서 사용자는 자유롭게 다른 조합을 선택할 수 있다."""
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)

    content_based = (autobiography.style_bible or {}).get("recommended_customization")
    if content_based:
        return {
            "tones": content_based["tones"],
            "structures": content_based["structures"],
            "concepts": content_based["concepts"],
            "source": "content_based",
            "reasoning": content_based.get("reasoning"),
        }

    tag_based = await _recommend_customization_from_tags(gateways, autobiography)
    return {**tag_based, "source": "tag_based", "reasoning": None}


async def generate_sample_previews(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> AutobiographyRecord:
    """
    사용자가 선택한 말투 2 × 구성 2 × 컨셉 2 = 8개 조합에 대해 각각
    맛보기 텍스트(200~400자)를 생성해 style_bible.customization.previews에 저장한다.

    8개를 전부 만든 뒤 한 번에 저장하지 않는다 — 조합별 메타데이터(tone/structure/
    concept/각 *_name)는 LLM 호출 없이 즉시 알 수 있으므로, 먼저 8개 자리표시자
    (preview_text=None, is_generating=True)를 커밋해 프론트가 즉시 8칸을 그릴 수
    있게 하고, 이후 조합이 하나 완성될 때마다 그 자리만 채워 매번 커밋한다
    (story_service.py의 세션 placeholder → 실제 카드 교체와 동일한 사상) — 그래야
    사용자가 8개를 한꺼번에 기다리지 않고 완성되는 대로 하나씩 볼 수 있다.
    """
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = dict(autobiography.style_bible or {})
    customization = style_bible.get("customization")
    if not customization:
        raise ValueError("먼저 커스터마이징 선택(save_customization_selection)을 완료해야 합니다.")

    tones = customization["tones"]
    structures = customization["structures"]
    concepts = customization["concepts"]
    style_bible_text = style_bible.get("content", "")

    # 이벤트 요약 구성 (상위 이벤트 10개 활용)
    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    event_summaries = "\n".join(
        f"- {event.one_line_summary} (시기: {event.occurred_at_label or '미상'})"
        for event in events[:10]
    )

    combos = list(itertools.product(tones, structures, concepts))
    previews = [
        {
            "tone": tone_key,
            "structure": structure_key,
            "concept": concept_key,
            "tone_name": prompts.TONE_OPTIONS[tone_key]["name"],
            "structure_name": prompts.STRUCTURE_OPTIONS[structure_key]["name"],
            "concept_name": prompts.CONCEPT_OPTIONS[concept_key]["name"],
            "preview_text": None,
            "is_generating": True,
        }
        for tone_key, structure_key, concept_key in combos
    ]
    customization["previews"] = previews
    style_bible["customization"] = customization
    await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()

    for index, (tone_key, structure_key, concept_key) in enumerate(combos):
        messages = prompts.build_sample_preview_prompt(
            tone_key=tone_key,
            structure_key=structure_key,
            concept_key=concept_key,
            style_bible=style_bible_text,
            event_summaries=event_summaries,
        )
        result = await llm_router.structured_completion(
            messages,
            schema_name="sample_preview",
            json_schema=prompts.SAMPLE_PREVIEW_SCHEMA,
            reasoning_effort="medium",
        )
        previews[index]["preview_text"] = result.get("preview_text", "")
        previews[index]["is_generating"] = False
        customization["previews"] = previews
        style_bible["customization"] = customization
        await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
        await gateways.commit()

    customization["previews_generated_at"] = _now_iso()
    style_bible["customization"] = customization
    autobiography = await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()
    return autobiography


async def get_sample_previews(
    gateways: Gateways, autobiography_id: uuid.UUID
) -> list[dict] | None:
    """생성된 샘플 미리보기를 반환. 아직 생성 전이면 None."""
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = autobiography.style_bible or {}
    customization = style_bible.get("customization", {})
    return customization.get("previews")


async def confirm_customization(
    gateways: Gateways,
    autobiography_id: uuid.UUID,
    *,
    tone: str,
    structure: str,
    concept: str,
) -> AutobiographyRecord:
    """8개 샘플 중 사용자가 선택한 최종 조합을 확정한다."""
    if tone not in prompts.TONE_OPTIONS:
        raise ValueError(f"유효하지 않은 말투 키: {tone}")
    if structure not in prompts.STRUCTURE_OPTIONS:
        raise ValueError(f"유효하지 않은 구성 키: {structure}")
    if concept not in prompts.CONCEPT_OPTIONS:
        raise ValueError(f"유효하지 않은 컨셉 키: {concept}")

    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    style_bible = dict(autobiography.style_bible or {})
    customization = style_bible.get("customization", {})

    customization["confirmed"] = {
        "confirmed_at": _now_iso(),
        "tone": tone,
        "structure": structure,
        "concept": concept,
    }
    style_bible["customization"] = customization
    autobiography = await gateways.autobiographies.update(autobiography_id, style_bible=style_bible)
    await gateways.commit()
    return autobiography


# --------------------------------------------------------------------------- #
# Phase 3: 이벤트 병합 · 중요도 산정 · 스타일 바이블                            #
# --------------------------------------------------------------------------- #

async def consolidate_autobiography(gateways: Gateways, user_id: uuid.UUID) -> AutobiographyRecord:
    """
    모든 인터뷰 세션 종료 후 호출되는 Phase 3 진입점. 순서가 중요하다 — 병합을 먼저
    끝내야 중복 흡수된 이벤트가 중요도 산정의 반복 언급 신호(mention_count)에 반영된다.
    """
    autobiography = await get_or_create_autobiography(gateways, user_id)

    consolidated_content = await _build_consolidated_content(gateways, user_id)
    await _merge_duplicate_events(gateways, user_id)
    await _score_importance(gateways, user_id)
    style_bible = await _generate_style_bible(gateways, user_id)

    if style_bible is not None:
        # 스타일 바이블이 막 만들어진 시점이라야 이 사람의 실제 문체·사건을 근거로
        # 한 콘텐츠 기반 커스터마이징 추천을 만들 재료가 갖춰진다 — 없으면(이벤트
        # 없음) None이 돌아오고, get_customization_recommendations가 태그 기반으로
        # 폴백한다.
        recommendation = await _generate_content_based_customization_recommendation(
            gateways, user_id, style_bible["content"]
        )
        if recommendation is not None:
            style_bible["recommended_customization"] = recommendation

    autobiography = await gateways.autobiographies.update(
        autobiography.id,
        status=AutobiographyStatus.CONSOLIDATED,
        consolidated_content=consolidated_content,
        style_bible=style_bible,
    )
    # User.current_stage: 목차 생성·챕터 조립(Phase 4)이 이제부터 시작되므로
    # "publishing" 단계로 전환한다(어디서도 갱신되지 않던 버그, 2026-07-12 발견 —
    # interview_service.create_session의 동일한 수정 참조).
    await gateways.users.update(user_id, current_stage=UserStage.PUBLISHING)
    await gateways.commit()
    return autobiography


async def _build_consolidated_content(gateways: Gateways, user_id: uuid.UUID) -> str:
    """Autobiography.consolidated_content: 완료된 세션의 산문을 시간순으로 이어붙인
    열람용 원본. LLM 입력으로 재사용하지 않는다(모델 docstring 참조)."""
    prose = await gateways.sessions.list_session_prose_by_user(user_id)
    return "\n\n".join(prose)


async def _merge_duplicate_events(gateways: Gateways, user_id: uuid.UUID) -> None:
    """
    Phase 3 이벤트 병합·정합성 검토(기획안). 임베딩 유사도는 병합 '후보' 탐색에만
    쓰고, 실제 병합 여부는 LLM 쌍별 판정으로 결정한다. 판정이 불확실하면 병합하지
    않는 것이 기본값이다(과병합은 인쇄 후 회복 불가, 과분리는 사용자 확인으로 즉시
    회복 가능하다는 리스크 비대칭 — prompts.EVENT_MERGE_JUDGE_SYSTEM_PROMPT 참조).
    """
    canonical_candidates = await gateways.events.list_mergeable(user_id)
    merged_ids: set[uuid.UUID] = set()

    for canonical in canonical_candidates:
        if canonical.id in merged_ids:
            continue  # 이전 반복에서 이미 다른 이벤트로 흡수됨

        candidates = await gateways.events.find_merge_candidates(
            user_id=user_id,
            exclude_event_id=canonical.id,
            embedding=canonical.embedding,
            max_distance=EVENT_MERGE_CANDIDATE_MAX_DISTANCE,
            limit=EVENT_MERGE_CANDIDATE_LIMIT,
        )
        # 같은 canonical에 대한 후보 판정들은 서로 독립이라 병렬로 돌린다(2026-07-18
        # — 순차 LLM 호출이 병합 단계의 주 병목이었다). canonical 간 순서는
        # merged_ids 의존성(이미 흡수된 이벤트 건너뛰기) 때문에 그대로 순차 유지.
        fresh_candidates = [c for c in candidates if c.id not in merged_ids]
        verdicts = await asyncio.gather(
            *(_judge_same_event(canonical, candidate) for candidate in fresh_candidates)
        )
        for candidate, same_event in zip(fresh_candidates, verdicts):
            if same_event:
                await gateways.events.mark_duplicate(candidate.id, duplicate_of_event_id=canonical.id)
                merged_ids.add(candidate.id)


async def _judge_same_event(event_a: EventRecord, event_b: EventRecord) -> bool:
    messages = prompts.build_event_merge_judge_prompt(
        event_a_summary=f"{event_a.one_line_summary} ({event_a.occurred_at_label or '시기 미상'})",
        event_b_summary=f"{event_b.one_line_summary} ({event_b.occurred_at_label or '시기 미상'})",
    )
    result = await llm_router.structured_completion(
        messages,
        schema_name="event_merge_judge",
        json_schema=prompts.EVENT_MERGE_JUDGE_SCHEMA,
        reasoning_effort="low",
    )
    return bool(result.get("same_event", False))


async def _score_importance(gateways: Gateways, user_id: uuid.UUID) -> None:
    """
    Phase 3 객관적 중요도 스코어링. LLM 주관 판단이 아니라 계산 가능한 신호의
    가중합 + 사용자 내 z-score 정규화(발화량 편차 보정)로 산정한다(기획안).
    importance_signals에 산출 근거를 남겨 "왜 이 사건이 목차에 들어갔는가"를
    재현 가능하게 설명한다.
    """
    events = await gateways.events.list_unmerged_verified(user_id)
    if not events:
        return

    lengths = [len(event.prose_paragraph) for event in events]
    mean_length = statistics.mean(lengths)
    stdev_length = statistics.pstdev(lengths)
    mention_counts = await gateways.events.count_mentions([event.id for event in events])

    updates: list[EventImportanceUpdate] = []
    for event in events:
        z_length = (len(event.prose_paragraph) - mean_length) / stdev_length if stdev_length > 0 else 0.0
        mention_count = mention_counts.get(event.id, 0) + 1  # +1: 본인 자신도 1회 언급으로 계산
        milestone = prompts.classify_life_milestone_category(
            f"{event.one_line_summary} {event.prose_paragraph}"
        )

        score = (
            WEIGHT_LENGTH_Z * z_length
            + WEIGHT_EMOTION_INTENSITY * (event.emotion_intensity or 0)
            + WEIGHT_MENTION_COUNT * (mention_count - 1)
            + (MILESTONE_BONUS if milestone else 0.0)
            + (MUST_INCLUDE_BONUS if event.is_must_include else 0.0)
        )
        updates.append(
            EventImportanceUpdate(
                event_id=event.id,
                importance_score=Decimal(str(round(score, 3))),
                importance_signals={
                    "raw_length": len(event.prose_paragraph),
                    "z_length": round(z_length, 3),
                    "emotion_intensity": event.emotion_intensity,
                    "mention_count": mention_count,
                    "life_milestone_category": milestone,
                    "is_must_include": event.is_must_include,
                },
                life_milestone_category=LifeMilestoneCategory(milestone) if milestone else None,
            )
        )

    await gateways.events.bulk_update_importance(updates)


async def _generate_style_bible(gateways: Gateways, user_id: uuid.UUID) -> dict | None:
    all_prose = await gateways.sessions.list_session_prose_by_user(user_id)
    if not all_prose:
        return None

    response = await llm_router.chat_completion(
        prompts.build_style_bible_prompt(all_session_prose=all_prose),
        reasoning_effort="medium",
    )
    return {"generated_at": _now_iso(), "content": response.choices[0].message.content or ""}


# --------------------------------------------------------------------------- #
# Phase 4: 동적 목차 · 하향식 집필 · 팩트체크 · 근거검증 · 등장인물 스캔        #
# --------------------------------------------------------------------------- #

async def generate_toc_candidates(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)

    events = await gateways.events.list_unmerged_verified(autobiography.user_id)
    if not events:
        raise ValueError("목차를 생성하려면 먼저 Phase 3(consolidate_autobiography)이 완료되어야 합니다.")

    summaries_block = "\n".join(
        f"- [중요도 {event.importance_score}] {event.one_line_summary} "
        f"(시기: {event.occurred_at_label or '미상'}, 감정: {event.emotion_tag or '미상'})"
        for event in events
    )

    # 커스터마이징이 확정돼 있으면 사용자가 선택한 구성(structure) 지시문을 주입한다.
    confirmed = _get_confirmed_customization(autobiography)
    if confirmed:
        toc_messages = prompts.build_customized_toc_prompt(
            event_summaries_with_scores=summaries_block,
            structure_key=confirmed["structure"],
        )
    else:
        toc_messages = prompts.build_toc_generation_prompt(
            event_summaries_with_scores=summaries_block
        )

    result_json = await llm_router.structured_completion(
        toc_messages,
        schema_name="toc_generation",
        json_schema=prompts.TOC_GENERATION_SCHEMA,
        reasoning_effort="medium",
    )
    toc_data = {
        "generated_at": _now_iso(),
        "candidates": [_normalize_toc_parts(c) for c in result_json["candidates"]],
        "selected_candidate_index": None,
    }
    autobiography = await gateways.autobiographies.update(autobiography_id, toc_data=toc_data)
    await gateways.commit()
    return autobiography


# Part 제목에 "Part 1:", "1부:", "제1부" 같은 번호 접두어가 다시 들어간 경우를
# 잡아낸다 — 프론트가 "{part_index}부. {part_title}"로 번호를 이미 붙이므로
# 그대로 두면 "1부. Part 1: ..." 처럼 중복 표기된다(2026-07-17 실사용 중 발견).
_PART_TITLE_PREFIX_PATTERN = re.compile(
    r"^(?:Part|PART)\s*\d+\s*[:.\-–—]?\s*|^제?\d+부\s*[:.\-–—]?\s*"
)

# Part당 챕터 최소 개수 — 사용자 확정치. TOC_GENERATION_SYSTEM_PROMPT도 같은
# 수치를 지시하지만, 실사용 중 프롬프트 지시만으로는 LLM이 이 제약을 어기는
# 사례가(예: 마지막 Part가 챕터 1개) 반복 확인돼 결정론적 보정을 추가했다.
_MIN_CHAPTERS_PER_PART = 3


def _strip_part_title_prefix(part_title: str) -> str:
    stripped = _PART_TITLE_PREFIX_PATTERN.sub("", part_title, count=1).strip()
    return stripped or part_title  # 접두어 제거 후 빈 문자열이면(전부 접두어였던 극단 케이스) 원본 유지


def _normalize_toc_parts(candidate: dict) -> dict:
    """TOC 후보 하나를 저장 전 결정론적으로 보정한다:
    1. 각 Part 제목에서 중복 번호 접두어를 제거한다(_strip_part_title_prefix).
    2. 챕터가 3개 미만인 Part를 인접 Part(첫 Part면 다음 Part, 그 외엔 이전
       Part)에 흡수시켜 제거하고, 남은 Part/챕터의 part_index를 1부터 다시
       연속 번호로 매긴다.

    Part가 1개 이하(episodic 예외 등 실제로 Part 구조가 없는 경우)면 제목
    접두어 제거만 하고 병합 로직은 건드리지 않는다 — 병합 대상 자체가 없다."""
    candidate = dict(candidate)
    parts = [dict(p) for p in candidate.get("parts") or []]
    chapters = [dict(c) for c in candidate.get("chapters") or []]

    for part in parts:
        part["part_title"] = _strip_part_title_prefix(part["part_title"])

    if len(parts) <= 1:
        candidate["parts"] = parts
        candidate["chapters"] = chapters
        return candidate

    parts.sort(key=lambda p: p["part_index"])

    def _chapter_count(part_index: int) -> int:
        return sum(1 for c in chapters if c.get("part_index") == part_index)

    while len(parts) > 1:
        violating = next((p for p in parts if _chapter_count(p["part_index"]) < _MIN_CHAPTERS_PER_PART), None)
        if violating is None:
            break
        position = parts.index(violating)
        target = parts[position - 1] if position > 0 else parts[position + 1]
        for chapter in chapters:
            if chapter.get("part_index") == violating["part_index"]:
                chapter["part_index"] = target["part_index"]
        parts.remove(violating)

    renumber = {part["part_index"]: new_index for new_index, part in enumerate(parts, start=1)}
    for part in parts:
        part["part_index"] = renumber[part["part_index"]]
    for chapter in chapters:
        if chapter.get("part_index") in renumber:
            chapter["part_index"] = renumber[chapter["part_index"]]

    candidate["parts"] = parts
    candidate["chapters"] = chapters
    return candidate


async def select_toc_candidate(
    gateways: Gateways, autobiography_id: uuid.UUID, candidate_index: int
) -> AutobiographyRecord:
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    if not autobiography.toc_data or not autobiography.toc_data.get("candidates"):
        raise ValueError("먼저 목차 후보를 생성해야 합니다(generate_toc_candidates).")

    candidates = autobiography.toc_data["candidates"]
    if not (0 <= candidate_index < len(candidates)):
        raise ValueError(f"candidate_index={candidate_index}가 후보 범위를 벗어났습니다(총 {len(candidates)}개).")

    chosen = candidates[candidate_index]

    book_synopsis = await _generate_book_synopsis(autobiography, chosen)
    title = await _generate_book_title(autobiography, chosen)

    # 챕터 시놉시스를 목차 확정 시점에 전 챕터 분량으로 미리 생성해 초안에 함께
    # 저장한다. 두 가지를 동시에 얻는다(2026-07-17): (1) 챕터당 ~20초짜리 최대
    # 단일 구간이 집필 임계 경로에서 빠지고, (2) write_chapter의 "직전 챕터 요약"이
    # 직전 챕터의 완성 본문 대신 이 시놉시스를 읽게 되어 챕터 간 직렬 의존이
    # 사라진다 — 프론트는 이미 전 챕터를 Promise.all로 동시에 큐잉하므로, 이
    # 변경으로 전체 집필 시간이 챕터 수와 무관해진다(워커 동시성 범위 내).
    # 이벤트 검색은 같은 DB 세션을 공유하므로 순차로 돌고(챕터당 ~1.6초, 대부분
    # 임베딩 HTTP), LLM 시놉시스 호출만 asyncio.gather로 병렬화한다.
    #
    # part_synopses 생성보다 먼저 실행한다 — Part 시놉시스도 이 검색 결과를
    # 근거로 받아야 한다(아래 _generate_part_synopses 참조). 예전엔 part_synopsis가
    # 사건 자료를 하나도 못 보고 book_synopsis·챕터 제목만 보고 지어냈는데, 이
    # 때문에 "옥스퍼드에서 태어났다"는 근거를 "도서관에서 태어났다"로 구체화해
    # 지어내는 환각이 실사용 중 확인됐다(2026-07-17) — 새 검색 호출 없이 이미
    # 여기서 구하는 chapter_events를 재사용해 근거를 붙여준다.
    # 임베딩 HTTP 호출은 서로 독립이라 전 챕터 분량을 병렬로 먼저 구하고, DB 검색만
    # 같은 세션을 공유하는 제약 때문에 순차로 돈다(2026-07-18 — 이전엔 임베딩까지
    # 챕터당 순차 실행이라 19장 기준 임계 경로가 ~30초씩 걸렸다).
    chapter_titles = [chapter_data.get("title") or "" for chapter_data in chosen["chapters"]]
    embedded_vectors = iter(
        await asyncio.gather(
            *(embeddings_client.embed_query(title) for title in chapter_titles if title)
        )
    )
    chapter_vectors = [next(embedded_vectors) if title else None for title in chapter_titles]

    chapter_events: list[list[EventRecord]] = []
    # 주의: 루프 변수를 title로 쓰면 위의 책 제목(title)을 가려버린다 — 실제로
    # 책 제목이 마지막 챕터 제목으로 저장되는 회귀를 테스트가 잡아냈다(2026-07-18).
    for chapter_title, vector in zip(chapter_titles, chapter_vectors):
        chapter_events.append(
            await _retrieve_events_for_chapter(
                gateways, autobiography.user_id, chapter_title, query_vector=vector
            )
        )

    # 배타적 배정: 같은 사건이 여러 챕터의 검색 결과에 들어 있으면 가장 관련도
    # 높은 챕터 하나에만 남긴다 — 이 배정 결과가 시놉시스 생성과 ChapterDraft
    # 저장(source_event_ids)에 함께 쓰여, 이후 write_chapter의 집필 재료와
    # 시놉시스의 설계 재료가 항상 일치한다. 이어서 시기 정합 보정: 검색 순위가
    # 시기를 무시하고 엉뚱한 챕터에 배정한 사건을 연도 중앙값 기준으로 재배치.
    retrieved_chapter_events = chapter_events
    chapter_events = _assign_events_to_chapters(retrieved_chapter_events)
    chapter_events = _rebalance_assignment_by_year(chapter_events, retrieved_chapter_events)

    part_synopses = await _generate_part_synopses(book_synopsis, chosen, chapter_events)
    if part_synopses:
        chosen = {
            **chosen,
            "parts": [
                {**part, "part_synopsis": part_synopses.get(part["part_index"], part["part_arc"])}
                for part in chosen["parts"]
            ],
        }
        candidates = [chosen if i == candidate_index else c for i, c in enumerate(candidates)]

    chapter_synopses = await asyncio.gather(
        *(
            _generate_chapter_synopsis(
                book_synopsis=book_synopsis,
                chapter_title=chapter_data.get("title") or f"{chapter_data['chapter_index']}장",
                event_summaries=[event.one_line_summary for event in events],
                connecting_thread=chapter_data.get("connecting_thread"),
                part_context=_part_context_from_selected(chosen, chapter_data["chapter_index"]),
                time_scope=_chapter_time_scope(events),
            )
            for chapter_data, events in zip(chosen["chapters"], chapter_events)
        )
    )

    # 재선택 시 이전 챕터 초안을 대체한다(idempotent).
    await gateways.chapters.replace_all(
        autobiography.id,
        [
            ChapterDraftCreateData(
                chapter_index=chapter_data["chapter_index"],
                title=chapter_data["title"],
                synopsis=synopsis,
                source_event_ids=[event.id for event in events],
            )
            for chapter_data, synopsis, events in zip(
                chosen["chapters"], chapter_synopses, chapter_events
            )
        ],
    )

    updated_toc = {**autobiography.toc_data, "candidates": candidates, "selected_candidate_index": candidate_index}

    autobiography = await gateways.autobiographies.update(
        autobiography_id, toc_data=updated_toc, book_synopsis=book_synopsis, title=title
    )
    await gateways.commit()
    return autobiography


async def _generate_part_synopses(
    book_synopsis: str, chosen: dict, chapter_events: list[list[EventRecord]]
) -> dict[int, str]:
    """chosen['parts']의 씨앗 part_arc를 book_synopsis 확정 후 더 풍부한 Part
    시놉시스로 확장한다. Part가 1개 이하(episodic 예외/비커스터마이징 폴백)면
    확장할 실익이 없어 빈 dict를 반환한다.

    chapter_events: chosen["chapters"]와 같은 순서로, 각 챕터가 select_toc_
    candidate에서 이미 검색해 둔 사건 목록 — 여기서 새로 검색하지 않고 Part별로
    묶어(같은 사건이 그 Part의 여러 챕터에서 검색되면 event.id로 중복 제거)
    Part 시놉시스 프롬프트에 실제 근거로 넘긴다. 예전엔 이 함수가 book_synopsis·
    챕터 제목만 보고 Part 시놉시스를 지어냈는데, 실제 사건 자료가 전혀 없다 보니
    "옥스퍼드에서 태어났다"는 근거를 "도서관 복도에서 태어났다"로 구체화해
    지어내는 환각이 실사용 중 확인됐다(2026-07-17) — 그 수정."""
    parts = chosen.get("parts") or []
    if len(parts) <= 1:
        return {}

    chapters_by_part: dict[int, list[str]] = {}
    events_by_part: dict[int, dict[uuid.UUID, str]] = {}
    for chapter_data, events in zip(chosen["chapters"], chapter_events):
        part_index = chapter_data["part_index"]
        chapters_by_part.setdefault(part_index, []).append(chapter_data["title"])
        bucket = events_by_part.setdefault(part_index, {})
        for event in events:
            bucket[event.id] = event.one_line_summary

    part_synopses: dict[int, str] = {}
    for part in parts:
        # max_tokens를 명시하지 않으면 API 서버 기본값에 맡기게 되는데, 실측 중
        # (2026-07-17) Part 4개 중 마지막 호출 하나가 실제 시놉시스 대신 모델의
        # 계획 서술("The user wants us to create...")만 담긴 응답을 반환한 사고가
        # 있었다 — reasoning_effort="high"가 max_tokens를 추론 토큰만으로
        # 소진해버린 챕터 집필 사고와 같은 계열의 문제로 보인다. 넉넉한 여유를 둔다.
        response = await llm_router.chat_completion(
            prompts.build_part_synopsis_prompt(
                book_synopsis=book_synopsis,
                part_title=part["part_title"],
                part_arc_seed=part["part_arc"],
                chapter_titles=chapters_by_part.get(part["part_index"], []),
                event_summaries=list(events_by_part.get(part["part_index"], {}).values()),
            ),
            reasoning_effort="low",
            max_tokens=4000,
        )
        part_synopses[part["part_index"]] = response.choices[0].message.content or part["part_arc"]
    return part_synopses


def _toc_text(selected_toc: dict) -> str:
    arc = selected_toc.get("narrative_arc")
    arc_block = f"[전체 뼈대]\n{arc}\n\n" if arc else ""
    chapters = selected_toc["chapters"]
    parts = selected_toc.get("parts") or []

    def _chapter_line(chapter: dict) -> str:
        return (
            f"{chapter['chapter_index']}. {chapter['title']} "
            f"({', '.join(chapter.get('theme_keywords', []))})"
            + (f" — 연결고리: {chapter['connecting_thread']}" if chapter.get("connecting_thread") else "")
        )

    # Part가 2개 이상일 때만 Part별로 묶어 렌더링한다 — episodic 예외(Part 1개)나
    # Part 필드 자체가 없는 구버전 toc_data는 기존과 동일한 평평한 렌더링으로
    # 폴백해야 하위 호환이 깨지지 않는다.
    if len(parts) > 1:
        parts_by_index = {part["part_index"]: part for part in parts}
        lines: list[str] = []
        current_part_index: int | None = None
        for chapter in chapters:
            part_index = chapter.get("part_index")
            if part_index != current_part_index:
                current_part_index = part_index
                part = parts_by_index.get(part_index)
                if part:
                    lines.append(f"\n[{part['part_index']}부. {part['part_title']}] — {part.get('part_arc', '')}")
            lines.append(f"  {_chapter_line(chapter)}")
        chapters_text = "\n".join(lines)
    else:
        chapters_text = "\n".join(_chapter_line(chapter) for chapter in chapters)

    return f"{arc_block}{chapters_text}"


def get_chapter_part_context(autobiography: AutobiographyRecord, chapter_index: int) -> dict | None:
    """toc_data에 저장된 선택 후보에서 이 챕터가 속한 Part의 컨텍스트(제목·
    시놉시스·Part 경계 여부·인접 Part 제목)를 찾는다. _chapter_connecting_thread와
    동일한 패턴(DB 컬럼 없이 toc_data JSON만 읽음) — pdf_service.py에서도 쓰이므로
    공개 함수로 둔다. toc_data/선택 후보가 없거나, parts가 1개 이하(episodic 예외
    또는 비커스터마이징 폴백), 이 chapter_index를 찾지 못했거나 part_index가 없는
    구버전 toc_data라면 None을 반환해 "Part 구조 없음"으로 취급한다."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return None
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return None
    return _part_context_from_selected(candidates[selected_index], chapter_index)


def _part_context_from_selected(selected: dict, chapter_index: int) -> dict | None:
    """get_chapter_part_context의 본체 — 선택된 목차 후보 dict를 직접 받는 변형.
    select_toc_candidate는 toc_data에 selected_candidate_index를 저장하기 전에
    (챕터 시놉시스 사전 생성 시점) Part 컨텍스트가 필요하므로 이 형태로 분리했다."""
    parts = selected.get("parts") or []
    if len(parts) <= 1:
        return None
    parts_by_index = {part["part_index"]: part for part in parts}
    chapters = selected.get("chapters", [])

    target = next((c for c in chapters if c.get("chapter_index") == chapter_index), None)
    if target is None or target.get("part_index") is None:
        return None
    part_index = target["part_index"]
    part = parts_by_index.get(part_index)
    if part is None:
        return None

    siblings = sorted(c["chapter_index"] for c in chapters if c.get("part_index") == part_index)
    ordered_part_indices = sorted(parts_by_index)
    pos = ordered_part_indices.index(part_index)

    return {
        "part_index": part_index,
        "part_title": part["part_title"],
        "part_synopsis": part.get("part_synopsis") or part.get("part_arc", ""),
        "is_part_opening": chapter_index == siblings[0],
        "is_part_closing": chapter_index == siblings[-1],
        "prev_part_title": (
            parts_by_index[ordered_part_indices[pos - 1]]["part_title"] if pos > 0 else None
        ),
        "next_part_title": (
            parts_by_index[ordered_part_indices[pos + 1]]["part_title"]
            if pos < len(ordered_part_indices) - 1
            else None
        ),
    }


def get_ordered_parts(autobiography: AutobiographyRecord) -> list[dict]:
    """선택된 후보의 parts를 part_index 오름차순으로 반환한다. Part 구조가 없으면
    (없음 또는 1개 이하) 빈 리스트 — pdf_service/프론트는 이를 "Part UI를 아예
    숨겨라"는 신호로 쓴다."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return []
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return []
    parts = candidates[selected_index].get("parts") or []
    if len(parts) <= 1:
        return []
    return sorted(parts, key=lambda part: part["part_index"])


def _chapter_connecting_thread(autobiography: AutobiographyRecord, chapter_index: int) -> str | None:
    """toc_data에 저장된 선택된 후보에서 이 챕터의 connecting_thread(목차 설계
    단계에서 정해진, 직전/다음 챕터와의 연결고리)를 찾는다. DB 스키마 변경 없이
    이미 저장된 toc_data(자유 JSON) 안의 값을 읽기만 한다 — 커스터마이징 이전에
    생성된 목차(connecting_thread 필드가 없는 구버전)라면 None."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return None
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return None
    for chapter in candidates[selected_index].get("chapters", []):
        if chapter.get("chapter_index") == chapter_index:
            return chapter.get("connecting_thread")
    return None


async def _generate_book_synopsis(autobiography: AutobiographyRecord, selected_toc: dict) -> str:
    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    response = await llm_router.chat_completion(
        prompts.build_book_synopsis_prompt(style_bible=style_bible_text, toc=_toc_text(selected_toc)),
        reasoning_effort="medium",
    )
    return _strip_synopsis_markdown_header(response.choices[0].message.content or "")


async def _generate_book_title(autobiography: AutobiographyRecord, selected_toc: dict) -> str:
    """표지·PDF 조판(app/services/pdf_service.py)에 그대로 노출되는 책 제목.
    toc/select 이전에는 Autobiography.title이 채워질 방법이 아예 없었다 — 목차가
    확정돼야 비로소 책 전체를 관통하는 제목을 지을 컨텍스트(스타일 바이블 + 목차)가
    갖춰지므로, book_synopsis와 같은 시점에 함께 생성한다."""
    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    result = await llm_router.structured_completion(
        prompts.build_book_title_prompt(style_bible=style_bible_text, toc=_toc_text(selected_toc)),
        schema_name="book_title",
        json_schema=prompts.BOOK_TITLE_SCHEMA,
        reasoning_effort="low",
    )
    return result["title"].strip()


async def _retrieve_events_for_chapter(
    gateways: Gateways,
    user_id: uuid.UUID,
    chapter_title: str,
    *,
    query_vector: list[float] | None = None,
) -> list[EventRecord]:
    """
    하이브리드 검색(의미 검색 + 키워드 정확 매칭). ChapterDraft는 theme_keywords를
    영속화하지 않으므로(이번 작업은 서비스 레이어로 범위를 한정했다 — DB 스키마
    확장은 별도 논의 대상) 챕터 제목을 쿼리로 사용하는 1차 근사치다. 제목 문자열만
    받는 이유: select_toc_candidate가 ChapterDraft 생성 전(목차 후보 dict 단계)에도
    같은 검색으로 챕터 시놉시스 재료를 소환해야 하기 때문.

    query_vector: 호출부가 제목 임베딩을 미리(예: 여러 챕터 분량을 병렬로) 구해뒀으면
    주입한다 — 없으면 여기서 단건 임베딩한다(select_toc_candidate가 19개 챕터를
    순차 임베딩하며 임계 경로가 늘어나던 문제의 병렬화 지점, 2026-07-18).

    두 축 모두 EventGateway.search_verified/search_by_keywords를 통하므로 Layer 1
    검증 게이트(verified=True, duplicate_of_event_id IS NULL)가 항상 적용된다.
    """
    query_text = chapter_title
    semantic_events: list[EventRecord] = []
    if query_text:
        if query_vector is None:
            query_vector = await embeddings_client.embed_query(query_text)
        semantic_events = await gateways.events.search_verified(
            user_id=user_id, query_embedding=query_vector, limit=CHAPTER_RETRIEVAL_LIMIT
        )

    keywords = [word for word in query_text.split() if len(word) >= 2]
    keyword_events = (
        await gateways.events.search_by_keywords(
            user_id=user_id, keywords=keywords, limit=CHAPTER_RETRIEVAL_LIMIT
        )
        if keywords
        else []
    )

    merged_ids = list(dict.fromkeys([e.id for e in semantic_events] + [e.id for e in keyword_events]))
    merged_ids = merged_ids[:CHAPTER_RETRIEVAL_LIMIT]
    if not merged_ids:
        return []
    return await gateways.events.list_by_ids(merged_ids)


# 배타적 배정 후 이벤트가 하나도 안 남은 챕터의 폴백: 원래 검색 결과 상위 N개를
# 그대로 남긴다(이때만 다른 챕터와의 중복 허용 — 빈 챕터로 집필이 통째로
# 무너지는 것보다 낫다).
_MIN_EVENTS_PER_CHAPTER_FALLBACK = 3

# 연도 기반 배정 보정(_rebalance_assignment_by_year)의 허용 이격 — 이벤트 연도가
# 배정된 챕터의 중앙값 연도와 이보다 크게 벌어져 있고, 검색 결과에 함께 올랐던
# 다른 챕터의 중앙값이 더 가까우면 그쪽으로 옮긴다. 2026-07-18 실측 오배정
# (1979년 루카스 임명식 사건이 1967년 중심의 3장에 배정) 대응.
_ASSIGNMENT_YEAR_TOLERANCE = 8

# 서기 연도 후보(1800~2099). occurred_at_label 자유 문자열에서의 폴백 추출용 —
# 정규화된 값은 이벤트 추출 단계의 labels.estimated_year_start가 우선이다.
_YEAR_IN_LABEL_PATTERN = re.compile(r"(1[89]\d{2}|20\d{2})")


def _event_estimated_year(event: EventRecord) -> int | None:
    """이벤트의 대표 연도. 1순위는 추출 단계가 정규화한 labels.estimated_year_start
    (2026-07-18 스키마 확장 — 신규 추출분부터 존재), 폴백은 occurred_at_label
    문자열 속 4자리 연도다(기존 데이터 커버)."""
    year = (event.labels or {}).get("estimated_year_start")
    if isinstance(year, int):
        return year
    if event.occurred_at_label:
        match = _YEAR_IN_LABEL_PATTERN.search(event.occurred_at_label)
        if match:
            return int(match.group(1))
    return None


def _sort_events_chronologically(events: list[EventRecord]) -> list[EventRecord]:
    """챕터 집필 프롬프트에 넘기기 직전, 사건을 시간순으로 정렬한다
    (write_chapter 참조 — 원래 순서는 중요도·검색 순위라 시간과 무관하다).
    연도를 알 수 없는 사건(_event_estimated_year가 None)은 완전히 배제하기보다
    안전하게 맨 뒤로 보낸다."""
    return sorted(
        events, key=lambda e: (_event_estimated_year(e) is None, _event_estimated_year(e) or 0)
    )


def _rebalance_assignment_by_year(
    assigned: list[list[EventRecord]],
    retrieved: list[list[EventRecord]],
) -> list[list[EventRecord]]:
    """배타적 배정(_assign_events_to_chapters)의 시기 정합 보정 1패스.

    순위 기반 배정은 중복은 막지만 "엉뚱한 챕터가 가져가는" 오배정은 못 막는다
    (2026-07-18 실측: 루카스 석좌 임명식(1979)이 검색 순위 때문에 아들 출생
    챕터(1967 중심)에 배정돼 그 챕터가 임명식 장면으로 시작함). 배정 스냅샷
    기준으로 챕터별 중앙값 연도를 구하고, 자기 챕터 중앙값과 허용 이격
    (_ASSIGNMENT_YEAR_TOLERANCE) 초과로 벌어진 이벤트를 — 원 검색 결과에 함께
    올랐던 챕터 중 중앙값이 가장 가까운 곳으로 옮긴다. 중앙값·이동 판단 모두
    보정 전 스냅샷 기준이라 입력이 같으면 결과도 항상 같다(결정론). 옮긴 뒤
    빈 챕터가 되는 이동은 하지 않는다."""
    year_lists = [[_event_estimated_year(e) for e in events] for events in assigned]
    medians: list[float | None] = []
    for years in year_lists:
        known = [y for y in years if y is not None]
        medians.append(statistics.median(known) if known else None)

    appears_in: dict[uuid.UUID, list[int]] = {}
    for pos, events in enumerate(retrieved):
        for event in events:
            appears_in.setdefault(event.id, []).append(pos)

    rebalanced = [list(events) for events in assigned]
    for pos, events in enumerate(assigned):
        median = medians[pos]
        if median is None:
            continue
        for event, year in zip(events, year_lists[pos]):
            if year is None:
                continue
            gap = abs(year - median)
            if gap <= _ASSIGNMENT_YEAR_TOLERANCE:
                continue
            best_pos, best_gap = pos, gap
            for alt in appears_in.get(event.id, []):
                alt_median = medians[alt]
                if alt == pos or alt_median is None:
                    continue
                alt_gap = abs(year - alt_median)
                if alt_gap < best_gap:
                    best_pos, best_gap = alt, alt_gap
            if best_pos != pos and len(rebalanced[pos]) > 1:
                rebalanced[pos].remove(event)
                rebalanced[best_pos].append(event)
                logger.info(
                    "배정 시기 보정: 사건(연도 %d)을 챕터 순번 %d(중앙값 %.0f) → %d(중앙값 %.0f)로 이동",
                    year, pos, median, best_pos, medians[best_pos],
                )
    return rebalanced


def _assign_events_to_chapters(
    chapter_events: list[list[EventRecord]],
) -> list[list[EventRecord]]:
    """챕터별 검색 결과(챕터 간 중복 포함)를 받아 각 이벤트를 정확히 한 챕터에만
    배정한다. 같은 사건(루카스 석좌 임명 등)이 여러 챕터에서 각각 소환돼 반복
    서술되는 문제(2026-07-17 호킹 계정 실측: 같은 결혼식 문장이 3장과 9장에 거의
    동일하게 등장)를 검색 레이어에서 결정론적으로 차단한다.

    배정 규칙: 이벤트가 여러 챕터의 검색 결과에 등장하면 검색 순위(리스트 내
    인덱스, 낮을수록 관련도 높음)가 가장 좋은 챕터가 가져간다. 동순위면 앞
    챕터 우선 — 입력이 같으면 결과도 항상 같다(전 챕터 병렬 집필과 무관하게
    결정적)."""
    best: dict[uuid.UUID, tuple[int, int]] = {}
    for chapter_pos, events in enumerate(chapter_events):
        for rank, event in enumerate(events):
            claim = (rank, chapter_pos)
            if event.id not in best or claim < best[event.id]:
                best[event.id] = claim

    assigned: list[list[EventRecord]] = []
    for chapter_pos, events in enumerate(chapter_events):
        kept = [
            event for rank, event in enumerate(events) if best[event.id] == (rank, chapter_pos)
        ]
        if not kept and events:
            kept = events[:_MIN_EVENTS_PER_CHAPTER_FALLBACK]
            logger.info(
                "챕터(순번 %d) 배타적 배정 결과가 비어 검색 상위 %d개로 폴백(중복 허용)",
                chapter_pos,
                len(kept),
            )
        assigned.append(kept)
    return assigned


def _chapter_time_scope(events: list[EventRecord]) -> str | None:
    """배정된 사건들의 시기 라벨(occurred_at_label)을 모아 챕터의 시간 범위 문자열로
    만든다. 집필·시놉시스 프롬프트에 "이 범위 밖 시기는 새 장면으로 서술 금지"
    지시와 함께 주입된다 — 한 챕터가 생애 후반부 전체를 흡수하는 스코프 폭주
    (호킹 계정 7장: 1963년 진단 챕터가 임종 직전까지 서술) 방지용."""
    labels = list(
        dict.fromkeys(
            event.occurred_at_label.strip()
            for event in events
            if event.occurred_at_label and event.occurred_at_label.strip()
        )
    )
    if not labels:
        return None
    return ", ".join(labels)


def _other_chapter_titles(autobiography: AutobiographyRecord, chapter_index: int) -> list[str]:
    """선택된 목차에서 이 챕터를 제외한 나머지 챕터 제목 목록. 집필 프롬프트에
    "다른 챕터에서 다룰 주제 — 이 챕터에서 본격 서술 금지"로 주입된다.
    _chapter_connecting_thread와 동일한 toc_data JSON 읽기 패턴."""
    toc_data = autobiography.toc_data
    if not toc_data or toc_data.get("selected_candidate_index") is None:
        return []
    candidates = toc_data.get("candidates", [])
    selected_index = toc_data["selected_candidate_index"]
    if not (0 <= selected_index < len(candidates)):
        return []
    return [
        f"{chapter['chapter_index']}장. {chapter.get('title') or ''}"
        for chapter in candidates[selected_index].get("chapters", [])
        if chapter.get("chapter_index") != chapter_index
    ]


async def _previous_chapter_summary(
    gateways: Gateways, autobiography_id: uuid.UUID, chapter_index: int
) -> str | None:
    """다음 챕터 집필에 넘길 직전 챕터 컨텍스트.

    1순위는 직전 챕터의 시놉시스(select_toc_candidate가 목차 확정 시점에 미리
    생성·저장) — 집필 계획이라 사건·감정·여운 신호가 이미 담겨 있고, 직전 챕터의
    '완성 본문'에 의존하지 않으므로 전 챕터를 동시에 집필해도(프론트가 이미
    Promise.all로 전 챕터를 한꺼번에 큐잉한다) 큐 순서와 무관하게 결정적으로
    같은 결과가 나온다. 예전 방식(완성 본문을 LLM으로 요약)은 챕터들을 직렬로
    묶는 병목이었을 뿐 아니라, 병렬 큐잉 시 직전 본문이 아직 없으면 조용히
    "(첫 챕터)"로 처리되는 비결정성이 있었다(2026-07-17).

    시놉시스가 없는 구버전 초안은 기존 방식(본문 요약, reasoning_effort="low")으로
    폴백한다 — 예전에는 previous.content[-1000:] 단순 절단을 썼다가 문장 중간에서
    잘린 조각이라 요약 호출로 바꾼 이력이 있다."""
    if chapter_index <= 1:
        return None
    previous = await gateways.chapters.get_by_index(autobiography_id, chapter_index - 1)
    if previous is None:
        return None
    if previous.chapter_synopsis:
        return previous.chapter_synopsis
    if not previous.content:
        return None
    response = await llm_router.chat_completion(
        prompts.build_chapter_recap_prompt(chapter_content=previous.content),
        reasoning_effort="low",
    )
    return response.choices[0].message.content or previous.content[-1000:]


def _total_flag_count(factcheck_report: dict, groundedness_report: dict) -> int:
    return len(factcheck_report.get("flags", [])) + len(groundedness_report.get("flags", []))


def _source_events_text(source_events: list[EventRecord]) -> str:
    return "\n".join(
        f"- {event.prose_paragraph}"
        for event in source_events
        if event.prose_paragraph and event.prose_paragraph.strip()
    )


def _strip_citation_tags(content: str, event_count: int) -> tuple[str, list[str]]:
    """집필 프롬프트의 근거 태그([E1]...)를 본문에서 제거하고, 유효한 태그가 하나도
    없는 문단 목록을 함께 반환한다(근거검증 판정자의 집중 검토 대상 — 순수 전환·감상
    문단일 수도 있으므로 자동 플래그가 아니라 '주의 지목'까지만 한다). 유효 태그 =
    1..event_count 범위의 사건 번호. 태그는 조판 전에 반드시 제거되어야 한다 —
    PDF에 그대로 인쇄되면 안 된다."""
    uncited_paragraphs: list[str] = []
    cleaned_paragraphs: list[str] = []
    for paragraph in content.split("\n\n"):
        cited_numbers = [int(n) for n in _CITATION_TAG_PATTERN.findall(paragraph)]
        cleaned = _CITATION_TAG_PATTERN.sub("", paragraph)
        # 태그 제거 자리에 남는 이중 공백/문장부호 앞 공백만 정돈한다(줄바꿈 유지).
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r" +([.,!?])", r"\1", cleaned).strip()
        if not cleaned:
            continue
        cleaned_paragraphs.append(cleaned)
        if not any(1 <= n <= event_count for n in cited_numbers):
            uncited_paragraphs.append(cleaned)
    return "\n\n".join(cleaned_paragraphs), uncited_paragraphs


def _flagged_items_for_repair(factcheck_report: dict, groundedness_report: dict) -> list[dict[str, str]]:
    """팩트체크 플래그(fact_type/raw_text)와 근거검증 플래그(sentence/reason)를
    수리 프롬프트가 받는 공통 모양({"sentence", "reason"})으로 정규화한다."""
    items: list[dict[str, str]] = []
    for flag in factcheck_report.get("flags", []):
        items.append(
            {
                "sentence": flag["raw_text"],
                "reason": f"본문 속 {flag['fact_type']} 표현이 근거 사건 어디에도 없음",
            }
        )
    for flag in groundedness_report.get("flags", []):
        items.append({"sentence": flag["sentence"], "reason": flag["reason"]})
    return items


async def _repair_chapter_content(
    *,
    chapter_content: str,
    factcheck_report: dict,
    groundedness_report: dict,
    source_events_text: str,
) -> str:
    """플래그된 문장만 근거에 맞게 고치거나 삭제하는 외과적 수리 호출. 전체
    재집필(reasoning_effort="high" + 5,500~7,000자 생성)보다 훨씬 싸고, 멀쩡한
    부분에 새 환각이 생길 위험이 없다. max_tokens는 챕터 전체를 되돌려주는
    호출이라 분량 지시 상향(2026-07-19) 이후의 챕터 길이에 맞춰
    _generate_chapter_content와 동일하게 둔다(수리 과정에서 잘리지 않도록)."""
    messages = prompts.build_chapter_repair_prompt(
        chapter_content=chapter_content,
        flagged_items=_flagged_items_for_repair(factcheck_report, groundedness_report),
        source_events_text=source_events_text,
    )
    response = await llm_router.chat_completion(messages, reasoning_effort="medium", max_tokens=24000)
    return response.choices[0].message.content or ""


async def write_chapter(gateways: Gateways, chapter_draft_id: uuid.UUID) -> ChapterDraftRecord:
    """
    Phase 4 하향식 집필의 챕터 단위 실행: [하이브리드 RAG 소환 → 본문 집필(근거 태그
    포함) → 팩트체크·근거검증(병렬) → 플래그 시 외과적 수리 루프 → 등장인물 스캔].

    챕터 시놉시스는 select_toc_candidate가 목차 확정 시점에 전 챕터 분량을 병렬로
    미리 생성해 두므로(챕터당 ~20초의 최대 단일 구간이 임계 경로에서 빠진다) 여기서는
    저장된 값을 읽기만 한다 — 구버전 초안(시놉시스 없음)만 폴백으로 즉석 생성한다.
    """
    chapter = await gateways.chapters.get(chapter_draft_id)
    if chapter is None:
        raise ValueError(f"ChapterDraft {chapter_draft_id} not found")
    autobiography = await gateways.autobiographies.get_by_id(chapter.autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {chapter.autobiography_id} not found")
    if not autobiography.book_synopsis:
        raise ValueError("먼저 목차를 선택해 책 전체 시놉시스를 생성해야 합니다(select_toc_candidate).")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")

    # select_toc_candidate가 배타적 배정으로 저장해 둔 사건이 있으면 그대로 쓴다
    # (재검색하면 다른 챕터에 배정된 사건이 다시 섞여 들어와 배정이 무의미해진다).
    # 빈 리스트 = 배정 이전 구버전 초안 — 기존 하이브리드 검색으로 폴백.
    if chapter.source_event_ids:
        retrieved_events = await gateways.events.list_by_ids(chapter.source_event_ids)
    else:
        retrieved_events = await _retrieve_events_for_chapter(
            gateways, autobiography.user_id, chapter.title or ""
        )
    # list_by_ids/_retrieve_events_for_chapter는 중요도·검색 순위로 정렬돼 있어
    # (EventGateway.list_by_ids의 ORDER BY importance_score), 그대로 집필
    # 프롬프트에 넘기면 시간 순서가 뒤죽박죽된 채 그대로 서술된다(2026-07-19
    # 실사용 중 확인 — 1965년 결혼 다음 문단에 17세 졸업이 나오는 등). LLM에게
    # "시간순으로 재배열하라"고 지시하는 것보다 코드에서 확정적으로 정렬하는
    # 편이 훨씬 안정적이다.
    retrieved_events = _sort_events_chronologically(retrieved_events)
    source_event_ids = [event.id for event in retrieved_events]
    time_scope = _chapter_time_scope(retrieved_events)
    other_titles = _other_chapter_titles(autobiography, chapter.chapter_index)

    chapter_synopsis = chapter.chapter_synopsis or await _generate_chapter_synopsis(
        book_synopsis=autobiography.book_synopsis,
        chapter_title=chapter.title or f"{chapter.chapter_index}장",
        event_summaries=[event.one_line_summary for event in retrieved_events],
        connecting_thread=_chapter_connecting_thread(autobiography, chapter.chapter_index),
        part_context=get_chapter_part_context(autobiography, chapter.chapter_index),
        time_scope=time_scope,
    )

    previous_summary = await _previous_chapter_summary(gateways, autobiography.id, chapter.chapter_index)

    # 커스터마이징이 확정돼 있으면 말투·컨셉 지시문을 챕터 집필에 주입한다.
    confirmed = _get_confirmed_customization(autobiography)
    content = await _generate_chapter_content(
        style_bible=style_bible_text,
        book_synopsis=autobiography.book_synopsis,
        chapter_synopsis=chapter_synopsis,
        previous_chapter_summary=previous_summary,
        retrieved_event_paragraphs=[event.prose_paragraph for event in retrieved_events],
        tone_key=confirmed["tone"] if confirmed else None,
        concept_key=confirmed["concept"] if confirmed else None,
        time_scope=time_scope,
        other_chapter_titles=other_titles,
    )
    if not content.strip():
        # 본문이 비어 있으면(예: reasoning_effort="high"가 max_tokens 예산을 추론
        # 토큰만으로 다 써버려 실제 본문을 못 받은 경우, 2026-07-17 실측) 검증도
        # 수리도 대상이 없다 — 같은 자료로 1회만 재집필을 시도한다. 빈 본문은
        # factcheck/groundedness 둘 다 "검증 대상 없음"으로 조용히 통과하므로
        # 아래 플래그 기반 수리 루프로는 이 실패를 못 잡는다.
        content = await _generate_chapter_content(
            style_bible=style_bible_text,
            book_synopsis=autobiography.book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_summary,
            retrieved_event_paragraphs=[event.prose_paragraph for event in retrieved_events],
            tone_key=confirmed["tone"] if confirmed else None,
            concept_key=confirmed["concept"] if confirmed else None,
            time_scope=time_scope,
            other_chapter_titles=other_titles,
        )

    # 분량 확장 패스는 폐기됐다(2026-07-19, prompts.py의 폐기 사유 주석 참조) —
    # "원본보다 길어지기만 하면 채택"이라는 약한 기준으로는 3,000자 문턱조차
    # 보장 못 하면서, 챕터당 순차 Solar 호출을 1회 더 얹어 처리 시간만 늘렸다
    # (집필 5~10분/챕터 실측). 대신 CHAPTER_WRITING_SYSTEM_PROMPT의 분량 지시
    # 자체를 장면 수·문단 수·문장 수 기준으로 바꿔 첫 호출에서 채우도록 했다.

    # 근거 태그([E1]...) 회수 — 태그가 하나도 없는 문단은 근거검증 판정자의 집중
    # 검토 대상으로 지목한다(집필 규약상 사실 서술 문단은 반드시 태그를 달아야 한다).
    content, uncited_paragraphs = _strip_citation_tags(content, len(retrieved_events))

    narrator = await gateways.users.get_by_id(autobiography.user_id)
    birth_year = narrator.birth_year if narrator else None
    # 팩트체크와 근거검증은 서로 독립적인 LLM 호출이라 병렬로 돌린다.
    factcheck_report, groundedness_report = await asyncio.gather(
        _run_factcheck(content, source_events=retrieved_events, birth_year=birth_year),
        _run_groundedness_check(
            content, source_events=retrieved_events, attention_paragraphs=uncited_paragraphs
        ),
    )

    # 외과적 수리 루프: 플래그가 남아 있으면 플래그된 문장만 고치는 수리 호출을
    # 최대 MAX_REPAIR_PASSES회 반복한다. 예전의 블라인드 전체 재집필(플래그 내용을
    # 전달하지 않고 같은 프롬프트로 다시 쓰기)은 수렴 보장이 없고 멀쩡한 부분에
    # 새 환각이 생길 수 있어 교체했다(2026-07-17). 수리 결과는 플래그가 실제로
    # 줄었을 때만 채택한다 — 줄지 않으면 더 반복해도 개선 가능성이 낮아 중단하고,
    # 잔여 플래그를 리포트에 남긴다(검토 화면에서 확인 가능).
    events_text = _source_events_text(retrieved_events)
    for _ in range(MAX_REPAIR_PASSES):
        if _total_flag_count(factcheck_report, groundedness_report) == 0:
            break
        repaired = await _repair_chapter_content(
            chapter_content=content,
            factcheck_report=factcheck_report,
            groundedness_report=groundedness_report,
            source_events_text=events_text,
        )
        repaired, _ = _strip_citation_tags(repaired, len(retrieved_events))
        if not repaired.strip():
            break
        repaired_factcheck, repaired_groundedness = await asyncio.gather(
            _run_factcheck(repaired, source_events=retrieved_events, birth_year=birth_year),
            _run_groundedness_check(repaired, source_events=retrieved_events),
        )
        if _total_flag_count(repaired_factcheck, repaired_groundedness) >= _total_flag_count(
            factcheck_report, groundedness_report
        ):
            break
        content, factcheck_report, groundedness_report = (
            repaired,
            repaired_factcheck,
            repaired_groundedness,
        )

    # 검수(교열) 패스: 오탈자·비문("속술했다"), 1인칭 이탈(3인칭 혼입), 종결어미
    # 격식 혼입("-습니다" 붕괴), 표현 남발, 시대착오적 묘사를 챕터 단위에서 고친다
    # (2026-07-18 호킹 계정 실측 결함들 — 최종 통일성 윤문(finalize)만으로는 검토
    # 화면에서 사용자가 읽는 윤문 전 본문에 그대로 노출된다). 사실 관계·문단
    # 구조는 건드리지 않는 교열 전용 프롬프트라 팩트체크 리포트는 재실행하지
    # 않는다. 빈 응답이거나 길이가 ±30% 넘게 변하면(교열이 아니라 개고를 한
    # 것) 원본을 유지한다.
    if content.strip():
        proofread_response = await llm_router.chat_completion(
            prompts.build_chapter_proofread_prompt(
                chapter_content=content,
                overused_terms=_count_overused_terms(content),
            ),
            reasoning_effort="low",
            # 챕터 전체를 되돌려주는 호출이라 분량 지시 상향(2026-07-19) 이후의
            # 챕터 길이에 맞춰 _generate_chapter_content와 동일하게 둔다.
            max_tokens=24000,
        )
        proofread = _strip_prompt_section_echo(
            proofread_response.choices[0].message.content or "", body_marker="[챕터 본문]"
        )
        if proofread and abs(len(proofread) - len(content)) <= len(content) * PROOFREAD_MAX_LENGTH_DRIFT:
            content = proofread
        elif proofread:
            logger.warning(
                "챕터 검수 패스 결과 길이가 원본 대비 30%%를 초과해 변해 원본 유지(원본 %d자 → 검수 %d자)",
                len(content),
                len(proofread),
            )

    chapter = await gateways.chapters.save_write_result(
        chapter_draft_id,
        ChapterDraftWriteResult(
            source_event_ids=source_event_ids,
            chapter_synopsis=chapter_synopsis,
            content=content,
            factcheck_report=factcheck_report,
            groundedness_report=groundedness_report,
            status=DraftStatus.REVIEWED,
        ),
    )
    await character_service.scan_and_classify_chapter(gateways, chapter=chapter, autobiography=autobiography)

    # 이미 최종 윤문이 끝난 책(final_content 존재)에서 사용자가 개별 챕터를 다시
    # 쓰면("이 챕터 다시 쓰기", 완성 후에도 유지되는 기능) final_content도 같이
    # 새로 조립한다 — 안 그러면 웹에서 보는 최종본만 옛 본문에 머문다(PDF는
    # chapter.content를 직접 읽으므로 원래 영향 없음, pdf_service 참조).
    if autobiography.final_content is not None:
        all_chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
        refreshed_final_content = _join_chapters_into_final_content(all_chapters)
        await gateways.autobiographies.update(autobiography.id, final_content=refreshed_final_content)

    await gateways.commit()
    return chapter


async def _generate_chapter_synopsis(
    *,
    book_synopsis: str,
    chapter_title: str,
    event_summaries: list[str],
    connecting_thread: str | None = None,
    part_context: dict | None = None,
    time_scope: str | None = None,
) -> str:
    response = await llm_router.chat_completion(
        prompts.build_chapter_synopsis_prompt(
            book_synopsis=book_synopsis,
            chapter_title=chapter_title,
            event_summaries=event_summaries,
            connecting_thread=connecting_thread,
            part_context=part_context,
            time_scope=time_scope,
        ),
        reasoning_effort="medium",
    )
    return response.choices[0].message.content or ""


async def _generate_chapter_content(
    *,
    style_bible: str,
    book_synopsis: str,
    chapter_synopsis: str,
    previous_chapter_summary: str | None,
    retrieved_event_paragraphs: list[str],
    tone_key: str | None = None,
    concept_key: str | None = None,
    time_scope: str | None = None,
    other_chapter_titles: list[str] | None = None,
) -> str:
    if tone_key and concept_key:
        messages = prompts.build_customized_chapter_writing_prompt(
            style_bible=style_bible,
            book_synopsis=book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_chapter_summary,
            retrieved_event_paragraphs=retrieved_event_paragraphs,
            tone_key=tone_key,
            concept_key=concept_key,
            time_scope=time_scope,
            other_chapter_titles=other_chapter_titles,
        )
    else:
        messages = prompts.build_chapter_writing_prompt(
            style_bible=style_bible,
            book_synopsis=book_synopsis,
            chapter_synopsis=chapter_synopsis,
            previous_chapter_summary=previous_chapter_summary,
            retrieved_event_paragraphs=retrieved_event_paragraphs,
            time_scope=time_scope,
            other_chapter_titles=other_chapter_titles,
        )
    # max_tokens=8000으로 처음 지정했다가 실사용 검증 중 본문이 통째로 빈
    # 문자열로 나오는 사고가 있었다(2026-07-17) — reasoning_effort="high"는
    # 눈에 보이지 않는 "추론 토큰"을 먼저 소비하고 그것도 max_tokens에
    # 포함되는데, 복잡한 챕터 프롬프트(전체 시놉시스+챕터 시놉시스+직전 챕터
    # 레시피+사건 문단 최대 20개)에서는 추론 토큰만으로 8000을 다 써버려
    # 실제 본문을 한 글자도 못 받는 경우가 실측됐다. 분량 지시 상향(2026-07-19,
    # 장면 6~7개·장면당 문단 8개 이상·문단당 4문장 이상 — 목표 약 5,500~7,000자)
    # 이후에는 추론 토큰 여유를 그만큼 더 넉넉히 둬야 같은 사고가 재현되지 않는다.
    response = await llm_router.chat_completion(messages, reasoning_effort="high", max_tokens=24000)
    return response.choices[0].message.content or ""


# 행정구역 명칭 변이(정식 명칭/구어체) 정규화. 기획안 예시("고향 바닷가"=부산 같은
# 완전한 의미 추론은 범위 밖이지만, 흔한 정식/구어 표기 차이는 결정론적으로 흡수한다.
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
# 긴 조사부터 매치해야 "에서부터"가 "에서"로 잘못 잘리는 일이 없다.
_PLACE_PARTICLES = ("에서부터", "으로부터", "에서는", "에서", "으로는", "부터", "까지", "에는", "으로", "에", "로")


def _normalize_place(text: str) -> str:
    normalized = text.strip()
    for particle in _PLACE_PARTICLES:
        if normalized.endswith(particle) and len(normalized) > len(particle):
            normalized = normalized[: -len(particle)]
            break
    return _PLACE_ALIASES.get(normalized, normalized)


# 나이의 고유어(순우리말) 표현 → 숫자. "스물다섯 살"처럼 십 단위+일 단위가 붙어
# 나오는 시니어 구어체 패턴을 커버한다(정확한 형태소 분석기 없이 결정론적으로 처리).
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


def _resolve_age_to_year(raw_text: str, *, birth_year: int | None) -> str | None:
    """
    '25세'(숫자) 또는 '스물다섯 살'(고유어) 형태의 나이 표현을 화자의 출생년도와
    더해 절대연도로 환산한다. 세는나이/만나이 차이로 ±1년 오차가 있을 수 있다는
    한계가 있다 — 대조 시 완전 일치가 아닌 근사치임을 감안할 것. birth_year를
    모르거나 나이 표현을 못 찾으면 None(호출부가 기존 문자열 대조로 폴백).
    """
    if birth_year is None:
        return None
    digit_match = _DIGIT_AGE_PATTERN.search(raw_text)
    if digit_match:
        return str(birth_year + int(digit_match.group(1)))
    word_match = _KOREAN_AGE_WORD_PATTERN.search(raw_text)
    if word_match:
        age = _KOREAN_AGE_TENS[word_match.group(1)] + _KOREAN_AGE_UNITS.get(word_match.group(2) or "", 0)
        return str(birth_year + age)
    return None


async def _run_factcheck(
    chapter_content: str, *, source_events: list[EventRecord], birth_year: int | None = None
) -> dict:
    """
    원문 대조 팩트체크(재추출-정규화-대조). 지명은 정식/구어 표기 변이를 정규화하고,
    나이 표현(숫자·고유어)은 화자 출생년도로 절대연도 환산해 대조한다("스물다섯 되던
    해"=1975년 같은 경우를 커버). 인명 별칭 매핑(가족 호칭·애칭 등)은 본격적인 개체
    연결이 필요해 이번 범위에서는 대소문자 무시 부분 문자열 매칭으로 남겨둔다.
    """
    if not chapter_content.strip():
        return {"checked_at": _now_iso(), "total_facts": 0, "unchecked_facts": 0, "flags": []}

    extraction = await llm_router.structured_completion(
        prompts.build_fact_reextraction_prompt(chapter_content=chapter_content),
        schema_name="fact_reextraction",
        json_schema=prompts.FACT_REEXTRACTION_SCHEMA,
        reasoning_effort="low",
    )
    facts = extraction.get("facts", [])

    expected_places = {_normalize_place(e.place.lower()) for e in source_events if e.place}
    expected_people = {e.people.lower() for e in source_events if e.people}
    expected_time_labels = {e.occurred_at_label.lower() for e in source_events if e.occurred_at_label}
    # 라벨(place/people/occurred_at_label)은 추출 단계의 요약이라 원문에 실재하는
    # 사실도 빠져 있는 경우가 많다 — 라벨 대조에 실패하면 소환된 사건 원문 문단에서
    # 한 번 더 찾아보고, 원문에 그대로 있으면 플래그하지 않는다(2026-07-18: 라벨에
    # 없는 실존 인물이 챕터마다 반복 플래그되던 구조적 오탐의 수정).
    source_prose_lower = " ".join(
        e.prose_paragraph.lower() for e in source_events if e.prose_paragraph
    )

    flags = []
    unchecked = 0
    for fact in facts:
        fact_type = fact["fact_type"]
        raw_text = fact["raw_text"].strip().lower()

        if fact_type == "quantity":
            # Event 모델에 수량 필드가 없어 대조 기준이 없다 — 오탐 방지를 위해 검증
            # 대상에서 제외하고 unchecked로만 집계한다.
            unchecked += 1
            continue
        if fact_type == "place":
            normalized = _normalize_place(raw_text)
            matched = any(normalized in exp or exp in normalized for exp in expected_places)
        elif fact_type == "year_or_age":
            matched = any(raw_text in exp or exp in raw_text for exp in expected_time_labels)
            if not matched:
                resolved_year = _resolve_age_to_year(fact["raw_text"], birth_year=birth_year)
                if resolved_year:
                    matched = any(resolved_year in exp for exp in expected_time_labels)
        else:  # person
            matched = any(raw_text in exp or exp in raw_text for exp in expected_people)

        if not matched and raw_text and raw_text in source_prose_lower:
            matched = True

        if not matched:
            flags.append(
                {"fact_type": fact_type, "raw_text": fact["raw_text"], "reason": "no_matching_source_label"}
            )

    return {
        "checked_at": _now_iso(),
        "total_facts": len(facts),
        "unchecked_facts": unchecked,
        "flags": flags,
    }


async def _run_groundedness_check(
    chapter_content: str,
    *,
    source_events: list[EventRecord],
    attention_paragraphs: list[str] | None = None,
) -> dict:
    """
    근거 검증(Groundedness Check). 챕터 본문이 소환된 사건들로 뒷받침되는지
    Solar LLM 판정으로 확인한다 — 어떤 사건으로도 뒷받침되지 않는 새로운
    사실·사건을 도입한 문장이 있으면 플래그한다.

    _run_factcheck(문자열 대조)는 "있는 사실이 변형됐는가"만 잡아낼 수 있어(precision),
    원문에 아예 없는 창작 진술은 놓친다 — 이 함수가 그 반대쪽(recall)을 담당한다
    (기획안 4절: "팩트체크(정밀 변형 탐지)와 근거 검증(무근거 창작 탐지)이 각각
    정밀도와 재현율을 분담하는 상보적 2층 구조").

    원래는 로컬 NLI(mDeBERTa) entailment로 문장 단위 대조를 했는데, 감각적
    묘사·내적 성찰 같은 정당한 정교화까지 거의 전부 플래그되는 도구 부적합 문제와
    챕터 하나에 20분 넘게 걸리는 속도 문제가 겹쳐 Solar LLM 판정(단일
    structured_completion)으로 교체했다(2026-07-17). 이때 "애매하면 무조건 통과"
    기준을 썼더니 이번엔 반대로 명백한 날조까지 통과하는 recall 붕괴가 왔다 —
    지금은 비대칭 기준(새 인물/사건/날짜·장소·결과는 애매해도 플래그, 감각 묘사·
    내적 독백은 통과) + reasoning_effort="medium"으로 판정하고, 플래그된 문장은
    2차 게이트(clients/groundedness.py — 원래 Upstage 전용 groundedness-check
    모델이었으나 폐기가 확인돼 solar-mini 이분 판정으로 대체됨, 2026-07-18)로
    한 번 더 확인해 "grounded"로 확정되면 철회한다(판정자 오탐 제거 — 완료 토큰
    한 단어짜리 병렬 호출이라 지연·비용이 거의 없다). 오탐이 남더라도 수리 단계가
    근거 기반으로 다시 쓸 뿐이라 환각이 늘지는 않는다.

    attention_paragraphs: 집필 근거 태그([En]) 없이 작성된 문단들 — 판정자가
    특히 집중해서 검토하도록 프롬프트에 지목한다.
    """
    if not chapter_content.strip() or not source_events:
        return {
            "checked": False,
            "flags": [],
            "note": "본문 또는 소환된 이벤트가 없어 검증을 생략함",
            "source_event_count": len(source_events),
        }

    source_events_text = _source_events_text(source_events)
    result = await llm_router.structured_completion(
        prompts.build_groundedness_judge_prompt(
            chapter_content=chapter_content,
            source_events_text=source_events_text,
            attention_paragraphs=attention_paragraphs,
        ),
        schema_name="groundedness_judge",
        json_schema=prompts.GROUNDEDNESS_JUDGE_SCHEMA,
        reasoning_effort="medium",
    )
    flags = [
        {"sentence": flag["sentence"], "reason": flag["reason"]} for flag in result.get("flags", [])
    ]

    dismissed_count = 0
    if flags:
        # 2차 게이트: 전용 groundedness-check 모델이 "grounded"라고 확정한 문장만
        # 철회한다(notGrounded/notSure/규약 밖 응답/호출 실패는 전부 플래그 유지 —
        # 검증 실패가 검증 통과로 둔갑하면 안 되므로 보수적으로 처리).
        async def _confirm(flag: dict) -> dict | None:
            try:
                verdict = await groundedness_client.check(
                    context=source_events_text, answer=flag["sentence"]
                )
            except Exception as exc:
                # 보수적 폴백(플래그 유지)은 유지하되 반드시 로그를 남긴다 —
                # 전용 groundedness-check 모델이 폐기된 뒤에도 이 예외가 조용히
                # 삼켜져 2차 게이트가 무력화된 채 몇 주간 아무도 몰랐던 전례가
                # 있다(2026-07-18 발견, clients/groundedness.py 참조).
                logger.warning("groundedness 2차 게이트 호출 실패(플래그 유지): %s", exc)
                return flag
            return None if verdict == groundedness_client.GROUNDED else flag

        confirmed = await asyncio.gather(*(_confirm(flag) for flag in flags))
        dismissed_count = sum(1 for item in confirmed if item is None)
        flags = [item for item in confirmed if item is not None]

    return {
        "checked": True,
        "flags": flags,
        "dismissed_by_groundedness_api": dismissed_count,
        "source_event_count": len(source_events),
    }


def _split_revised_manuscript_by_chapter(
    revised: str, chapter_indexes: list[int]
) -> dict[int, str] | None:
    """윤문 응답을 `<<<CHAPTER n>>>` 마커로 챕터별 본문으로 나눈다. 마커 집합이
    입력 챕터 목록과 정확히 일치하지 않으면(누락/중복/새 마커) None — 호출부가
    "윤문 전 챕터 유지 + 전문만 final_content" 폴백으로 처리한다.

    각 조각에서 챕터 헤더(`[N장. 제목]`) 줄과 PART 마커는 제거한다 — 제목·Part는
    별도 필드/toc_data로 관리되므로 chapter.content에는 순수 본문만 남아야
    PDF 조판(pdf_service)이 이중 표기 없이 그대로 쓸 수 있다."""
    matches = list(_CHAPTER_MARKER_PATTERN.finditer(revised))
    found_indexes = [int(m.group(1)) for m in matches]
    if found_indexes != chapter_indexes:
        return None

    by_index: dict[int, str] = {}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(revised)
        chunk = revised[start:end]
        chunk = _PART_MARKER_PATTERN.sub("", chunk)
        chunk = _CHAPTER_HEADER_PATTERN.sub("", chunk, count=1)
        chunk = chunk.strip()
        if not chunk:
            return None
        by_index[found_indexes[i]] = chunk
    return by_index


def _join_chapters_into_final_content(chapters: list[ChapterDraftRecord]) -> str:
    """챕터별 본문을 book 전체 final_content로 조립하는 단일 진실 원천 — 최초
    윤문(finalize_manuscript)과 이후의 모든 챕터 단위 변경(직접 수정
    edit_chapter_content, 완성 후 AI 재집필 write_chapter)이 이 함수 하나로
    final_content를 다시 만든다. 형식이 두 곳 이상에 흩어져 있으면 한쪽만
    고쳤을 때 웹 열람과 PDF가 다시 어긋날 위험이 있어(2026-07-18에 고쳤던 바로
    그 문제) 반드시 이 헬퍼를 거치게 한다."""
    return "\n\n".join(
        f"[{chapter.chapter_index}장. {chapter.title}]\n{chapter.content}" for chapter in chapters
    )


async def edit_chapter_content(
    gateways: Gateways, autobiography_id: uuid.UUID, chapter_draft_id: uuid.UUID, content: str
) -> AutobiographyRecord:
    """완성된 자서전의 챕터 본문을 사용자가 직접 고쳐 쓴다(2026-07-18, '나의 자서전'
    직접 수정 기능). AI 재집필(write_chapter)과 달리 LLM·외부 API 호출이 전혀
    없는 순수 텍스트 저장이라 요청이 즉시 끝난다 — 느린 외부 호출을 기다리며 DB
    세션을 오래 붙잡다 Supabase가 idle 커넥션을 끊어버리는 문제
    (interview_service.add_user_turn 모듈 docstring 참조: 예전에 세션 대화 저장
    경로에서 실제로 겪었던 사고)가 애초에 발생할 여지가 없다 — 이 함수 전체가
    조회 몇 번 + 텍스트 조립 + 커밋 하나뿐이다.

    아직 최종 윤문(finalize_manuscript)이 끝나지 않은 자서전은 이 경로를 쓰지
    않는다 — 그 단계에서는 챕터별 검토·재집필만 가능하고, 최종본이 나온 뒤에야
    "완성된 자서전을 직접 고친다"는 개념이 성립한다(호출부인 API 라우터가 이
    선행 조건을 확인한다)."""
    chapter = await get_chapter_draft(gateways, chapter_draft_id)
    if chapter is None or chapter.autobiography_id != autobiography_id:
        raise ValueError(f"자서전 {autobiography_id}에 속한 챕터 {chapter_draft_id}를 찾을 수 없습니다.")

    await gateways.chapters.update_content(chapter_draft_id, content)
    chapters = await gateways.chapters.list_by_autobiography(autobiography_id)
    final_content = _join_chapters_into_final_content(chapters)

    autobiography = await gateways.autobiographies.update(autobiography_id, final_content=final_content)
    await gateways.commit()
    return autobiography


# finalize_manuscript을 여러 번의 작은 호출로 나누는 배치 크기 상한(2026-07-19).
# 책 전체(챕터 19개, 53,692자 실측)를 한 번에 보냈다가 API 타임아웃(90초)으로
# 실패하는 사고가 실사용 중 재현됐다 — Part 경계를 우선 배치 경계로 삼되(Part
# 전환은 원래도 "매끄러운 이음매"가 아니라 "국면 전환"으로 다뤄지는 지점이라
# CHAPTER_WRITING_SYSTEM_PROMPT 참조 — 배치를 나눠도 실질적 손실이 작다), 한
# Part가 이 값을 넘으면(실측 계정에서 Part 하나가 챕터 10개였던 사례 참조) 그
# 안에서 순서대로 한 번 더 쪼갠다. Part 구조가 없는 책(단일 흐름)은 전체를
# 하나의 그룹으로 보고 동일한 상한을 적용해 같은 방식으로 나뉜다.
_FINALIZE_BATCH_MAX_CHAPTERS = 5

# 배치 하나의 최대 출력 토큰 — 원래 전체 호출(최대 19장 x 6,000자)의 48000보다
# 작지만, 배치 하나(최대 5장 x 6,000자 = 30,000자)에는 여전히 넉넉하다.
_FINALIZE_BATCH_MAX_TOKENS = 32000

# 배치당 호출 타임아웃(초) — 전역 기본값(90초, app/clients/base.py)은 다른 모든
# 호출(빠르게 실패해야 하는 것들)을 보호하는 의도적 설계라 그대로 두고, 이
# 호출에만 개별적으로 늘린다. 배치로 나눠 입출력이 작아져도 Solar 자체의 응답
# 속도가 실사용 중 들쭉날쭉했던 사례(예: 근거검증 판정 하나에 46초)가 있어
# 안전 마진을 크게 둔다.
_FINALIZE_BATCH_TIMEOUT_SECONDS = 180.0


def _group_chapters_for_finalize(
    autobiography: AutobiographyRecord, chapters: list[ChapterDraftRecord]
) -> list[list[ChapterDraftRecord]]:
    """finalize_manuscript의 통일성 윤문 호출을 배치로 나누기 위해 챕터를
    묶는다. Part 경계를 절대 넘지 않고(같은 배치에 서로 다른 Part의 챕터가
    섞이지 않음), 한 Part가 _FINALIZE_BATCH_MAX_CHAPTERS를 넘으면 그 안에서
    순서대로 다시 쪼갠다. Part 구조가 아예 없으면(get_chapter_part_context가
    모든 챕터에 None을 반환) 전체를 하나의 그룹으로 보고 동일하게 나눈다."""
    groups_by_key: dict[int | None, list[ChapterDraftRecord]] = {}
    order: list[int | None] = []
    for chapter in chapters:
        part_context = get_chapter_part_context(autobiography, chapter.chapter_index)
        key = part_context["part_index"] if part_context else None
        if key not in groups_by_key:
            groups_by_key[key] = []
            order.append(key)
        groups_by_key[key].append(chapter)

    batches: list[list[ChapterDraftRecord]] = []
    for key in order:
        group = groups_by_key[key]
        for start in range(0, len(group), _FINALIZE_BATCH_MAX_CHAPTERS):
            batches.append(group[start : start + _FINALIZE_BATCH_MAX_CHAPTERS])
    return batches


async def _finalize_batch(
    gateways: Gateways,
    *,
    autobiography: AutobiographyRecord,
    batch: list[ChapterDraftRecord],
    style_bible_text: str,
    confirmed: dict | None,
) -> None:
    """배치 하나(대개 Part 하나, 또는 너무 큰 Part의 일부)를 윤문하고 챕터별로
    되써넣는다. 호출 실패(타임아웃 포함)나 마커 파싱 실패 시 예외를 전파하지
    않고 이 배치의 챕터들만 원본을 유지한다 — 배치 하나의 실패가 다른 배치나
    최종본 생성 전체를 막지 않는다(로그만 남김)."""
    manuscript_blocks: list[str] = []
    for chapter in batch:
        part_context = get_chapter_part_context(autobiography, chapter.chapter_index)
        if part_context and part_context["is_part_opening"]:
            manuscript_blocks.append(f"=== PART {part_context['part_index']}: {part_context['part_title']} ===")
        manuscript_blocks.append(
            f"<<<CHAPTER {chapter.chapter_index}>>>\n"
            f"[{chapter.chapter_index}장. {chapter.title}]\n{chapter.content}"
        )
    batch_manuscript = "\n\n".join(manuscript_blocks)

    if confirmed:
        revision_messages = prompts.build_customized_unity_revision_prompt(
            style_bible=style_bible_text,
            full_manuscript=batch_manuscript,
            tone_key=confirmed["tone"],
            concept_key=confirmed["concept"],
        )
    else:
        revision_messages = prompts.build_unity_revision_prompt(
            style_bible=style_bible_text, full_manuscript=batch_manuscript
        )

    chapter_indexes = [chapter.chapter_index for chapter in batch]
    try:
        response = await llm_router.chat_completion(
            revision_messages,
            reasoning_effort="high",
            max_tokens=_FINALIZE_BATCH_MAX_TOKENS,
            timeout=_FINALIZE_BATCH_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning(
            "통일성 윤문 배치 호출 실패(챕터 %s) — 이 배치는 윤문 전 본문을 그대로 유지한다.",
            chapter_indexes,
            exc_info=True,
        )
        return

    revised = response.choices[0].message.content or ""
    if not revised.strip():
        return

    revised_by_index = _split_revised_manuscript_by_chapter(revised, chapter_indexes)
    if revised_by_index is None:
        logger.warning(
            "통일성 윤문 배치 응답의 챕터 마커가 어긋나(챕터 %s) — 이 배치는 "
            "윤문 전 본문을 그대로 유지한다.",
            chapter_indexes,
        )
        return

    for chapter in batch:
        await gateways.chapters.update_content(chapter.id, revised_by_index[chapter.chapter_index])


async def finalize_manuscript(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    """
    Phase 4 통일성 윤문 패스: 전 챕터 생성 후 인접 챕터 경계부와 스타일 바이블을
    함께 검토하는 리비전을 수행한다. 사실 관계·순서는 변경하지 않는다.

    책 전체를 한 번의 호출에 넣지 않고 Part 단위(너무 큰 Part는 더 쪼갬 —
    _group_chapters_for_finalize)로 나눠 여러 번 호출한다(2026-07-19) — 이전에는
    챕터 19개(53,692자)를 한 번에 보내다 API 타임아웃(90초)으로 실패하는 사고가
    실사용 중 재현됐다. 배치로 나누면 호출당 입출력이 훨씬 작아지고, 배치별
    타임아웃도 넉넉히 늘려(_FINALIZE_BATCH_TIMEOUT_SECONDS) 이중으로 방어한다.
    대가는 서로 다른 배치(대개 Part 경계)에 걸친 문체 다듬기는 하지 못한다는
    것인데, Part 경계는 원래도 매끄러운 이음매가 아니라 국면 전환으로 다뤄지는
    지점이라 실질적 손실은 작다. 배치 하나가 실패해도(_finalize_batch 참조)
    그 배치만 원본을 유지하고 나머지는 정상 반영되는 부분 성공을 허용한다.

    윤문 결과는 final_content에만 저장되는 게 아니라 챕터별로 파싱해
    chapter.content에도 되써넣는다(2026-07-18) — PDF 조판이 이 값을 직접
    읽으므로 웹 열람과 실물 책 텍스트가 항상 일치한다.
    """
    autobiography = await get_autobiography_by_id(gateways, autobiography_id)
    chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    if not chapters or any(chapter.content is None for chapter in chapters):
        raise ValueError("모든 챕터의 집필(write_chapter)이 끝난 뒤에 최종 윤문을 수행할 수 있습니다.")

    style_bible_text = (autobiography.style_bible or {}).get("content", "")
    # 커스터마이징이 확정돼 있으면 말투·컨셉 일관성을 윤문에도 반영한다.
    confirmed = _get_confirmed_customization(autobiography)

    batches = _group_chapters_for_finalize(autobiography, chapters)
    for batch in batches:
        await _finalize_batch(
            gateways,
            autobiography=autobiography,
            batch=batch,
            style_bible_text=style_bible_text,
            confirmed=confirmed,
        )

    for chapter in chapters:
        await gateways.chapters.mark_finalized(chapter.id)

    # 배치별로 되써넣어진 최신 챕터 내용을 다시 읽어 final_content를 조립한다
    # (배치 실패로 원본이 유지된 챕터도 그대로 섞여 들어간다 — 부분 성공).
    refreshed_chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    final_content = _join_chapters_into_final_content(refreshed_chapters)

    # AutobiographyStatus.PUBLISHED는 지금까지 enum 값만 정의돼 있고 실제로는
    # 어디서도 설정되지 않던 죽은 값이었다(2026-07-12 발견) — 최종 윤문(이 함수)이
    # 끝나 열람 가능한 완성본이 나오는 시점이 "최종 출판 완료"의 자연스러운 기준이다
    # (PDF 조판·POD는 그 이후의 별도 내보내기 단계).
    autobiography = await gateways.autobiographies.update(
        autobiography_id, final_content=final_content, status=AutobiographyStatus.PUBLISHED
    )
    await gateways.users.update(autobiography.user_id, current_stage=UserStage.PUBLISHED)
    await gateways.commit()
    return autobiography
