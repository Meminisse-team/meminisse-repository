import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, GatewaysDep, require_self
from app.gateways.dto import AutobiographyRecord, UserRecord
from app.schemas.autobiography import (
    AutobiographyRead,
    ChapterDraftRead,
    TocCandidateSelect,
)
from app.schemas.character import CharacterRead, RetainRealNameRequest
from app.services import autobiography_service, character_service

router = APIRouter(prefix="/autobiographies", tags=["autobiographies"])


async def _require_own_autobiography(
    gateways: GatewaysDep, autobiography_id: uuid.UUID, current_user: UserRecord
) -> AutobiographyRecord:
    """autobiography_id로 접근하는 모든 하위 엔드포인트(목차/챕터/등장인물)의 공통
    소유권 게이트. 존재하지 않거나 남의 자서전이면 둘 다 404로 응답해(그 자서전이
    실재하는지 자체를 숨김) 존재 여부를 통한 정보 노출을 막는다."""
    autobiography = await gateways.autobiographies.get_by_id(autobiography_id)
    if autobiography is None or autobiography.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "자서전을 찾을 수 없습니다.")
    return autobiography


@router.get("/{user_id}", response_model=AutobiographyRead)
async def get_autobiography(
    user_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> AutobiographyRead:
    require_self(current_user, user_id)
    autobiography = await autobiography_service.get_or_create_autobiography(gateways, user_id)
    return AutobiographyRead.model_validate(autobiography)


@router.post("/{user_id}/consolidate", status_code=status.HTTP_202_ACCEPTED)
async def consolidate(user_id: uuid.UUID, current_user: CurrentUserDep) -> dict:
    """
    Phase 3(이벤트 병합·중요도 산정·스타일 바이블) 트리거. 여러 차례의 LLM 호출이
    이어지는 무거운 연산이라 Celery 워커에 위임하고 즉시 202를 반환한다. 완료 여부는
    GET /{user_id}의 status 필드가 CONSOLIDATED로 바뀌는 것으로 폴링한다.
    """
    require_self(current_user, user_id)
    from app.workers.tasks import consolidate_autobiography as consolidate_task

    consolidate_task.delay(str(user_id))
    return {"detail": "Phase 3 consolidation queued"}


@router.post("/{autobiography_id}/toc/generate", response_model=AutobiographyRead)
async def generate_toc(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> AutobiographyRead:
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    try:
        autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AutobiographyRead.model_validate(autobiography)


@router.post("/{autobiography_id}/toc/select", response_model=AutobiographyRead)
async def select_toc(
    autobiography_id: uuid.UUID,
    payload: TocCandidateSelect,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> AutobiographyRead:
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    try:
        autobiography = await autobiography_service.select_toc_candidate(
            gateways, autobiography_id, payload.candidate_index
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AutobiographyRead.model_validate(autobiography)


@router.get("/{autobiography_id}/chapters", response_model=list[ChapterDraftRead])
async def list_chapters(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> list[ChapterDraftRead]:
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography_id)
    return [ChapterDraftRead.model_validate(chapter) for chapter in chapters]


@router.post(
    "/{autobiography_id}/chapters/{chapter_draft_id}/write", status_code=status.HTTP_202_ACCEPTED
)
async def write_chapter(
    autobiography_id: uuid.UUID,
    chapter_draft_id: uuid.UUID,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> dict:
    """챕터 단위 하향식 집필(시놉시스·본문·팩트체크·근거검증·등장인물 스캔) 트리거."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    chapter = await autobiography_service.get_chapter_draft(gateways, chapter_draft_id)
    if chapter is None or chapter.autobiography_id != autobiography_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "해당 자서전에 속한 챕터를 찾을 수 없습니다.")

    from app.workers.tasks import write_chapter as write_chapter_task

    write_chapter_task.delay(str(chapter_draft_id))
    return {"detail": "Chapter writing queued"}


@router.post("/{autobiography_id}/finalize", status_code=status.HTTP_202_ACCEPTED)
async def finalize(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> dict:
    """전 챕터 집필 완료 후 통일성 윤문 패스 트리거.

    인증 작업 이전에는 이 엔드포인트가 autobiography_id 존재 여부조차 확인하지
    않고 곧바로 Celery에 큐잉했다(존재하지 않는 ID를 넣어도 202가 나가고, 실제
    실패는 워커 내부에서만 조용히 발생) — 소유권 검증을 추가하는 김에 사전 조회로
    이 문제도 함께 바로잡았다."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    from app.workers.tasks import finalize_manuscript as finalize_task

    finalize_task.delay(str(autobiography_id))
    return {"detail": "Manuscript finalization queued"}


@router.get("/{autobiography_id}/characters", response_model=list[CharacterRead])
async def list_characters(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> list[CharacterRead]:
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    characters = await character_service.list_characters(gateways, autobiography_id)
    return [CharacterRead.model_validate(character) for character in characters]


@router.post(
    "/{autobiography_id}/characters/{character_id}/retain-real-name", response_model=CharacterRead
)
async def retain_real_name(
    autobiography_id: uuid.UUID,
    character_id: uuid.UUID,
    payload: RetainRealNameRequest,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> CharacterRead:
    """
    전수 가명화 기본값(opt-out)을 뒤집는 유일한 경로. 인물 단위 법적 책임 고지에
    대한 유효한 ConsentRecord(DISCLOSURE_REALNAME)가 없으면 409로 거부된다.
    """
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    character = await character_service.get_character(gateways, character_id)
    if character is None or character.autobiography_id != autobiography_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "해당 자서전에 속한 인물을 찾을 수 없습니다.")
    try:
        character = await character_service.retain_real_name(
            gateways, character_id, notice_version=payload.notice_version
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return CharacterRead.model_validate(character)
