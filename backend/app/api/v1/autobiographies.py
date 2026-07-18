import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, GatewaysDep, require_self
from app.gateways.dto import AutobiographyRecord, UserRecord
from app.schemas.autobiography import (
    AutobiographyRead,
    ChapterContentUpdate,
    ChapterDraftRead,
    CustomizationConfirmRequest,
    CustomizationOptionItem,
    CustomizationOptionsResponse,
    CustomizationRecommendationResponse,
    CustomizationSelectionRequest,
    PhotoPlacementsUpdate,
    SamplePreviewItem,
    SamplePreviewsResponse,
    TocCandidateSelect,
)
from app.schemas.character import CharacterRead, RetainRealNameRequest
from app.services import autobiography_service, character_service
from app.agents import prompts

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
    completed_session_count = await gateways.sessions.count_completed_by_user(user_id)
    data = AutobiographyRead.model_validate(autobiography)
    return data.model_copy(update={"completed_session_count": completed_session_count})


@router.get("/{user_id}/finished", response_model=list[AutobiographyRead])
async def list_finished_autobiographies(
    user_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> list[AutobiographyRead]:
    """"나의 책장" 전용 — 이 유저가 완성한 자서전 전체(최신순)."""
    require_self(current_user, user_id)
    autobiographies = await autobiography_service.list_finished_autobiographies(gateways, user_id)
    return [AutobiographyRead.model_validate(a) for a in autobiographies]


@router.post("/{user_id}/consolidate", status_code=status.HTTP_202_ACCEPTED)
async def consolidate(user_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep) -> dict:
    """
    Phase 3(이벤트 병합·중요도 산정·스타일 바이블) 트리거. 여러 차례의 LLM 호출이
    이어지는 무거운 연산이라 Celery 워커에 위임하고 즉시 202를 반환한다. 완료 여부는
    GET /{user_id}의 status 필드가 CONSOLIDATED로 바뀌는 것으로 폴링한다.

    재료(완료된 세션)가 너무 적으면 애초에 큐잉하지 않는다 — Celery 태스크
    안에서 실패시키면 프론트에 즉시 전달할 방법이 없어, 여기 라우터 레벨에서
    먼저 막는다(2026-07-17 제품 결정 — 최소 50개).
    """
    require_self(current_user, user_id)
    completed_session_count = await gateways.sessions.count_completed_by_user(user_id)
    if completed_session_count < autobiography_service.MIN_COMPLETED_SESSIONS_FOR_AUTOBIOGRAPHY:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"아직 이야기가 충분히 쌓이지 않았어요. 최소 "
            f"{autobiography_service.MIN_COMPLETED_SESSIONS_FOR_AUTOBIOGRAPHY}개 이상 답변한 뒤 다시 시도해주세요"
            f"(현재 {completed_session_count}개).",
        )
    from app.workers.tasks import consolidate_autobiography as consolidate_task

    consolidate_task.delay(str(user_id))
    return {"detail": "Phase 3 consolidation queued"}


# --------------------------------------------------------------------------- #
# 자서전 커스터마이징 — 말투·구성·컨셉 선택 / 미리보기 / 확정                    #
# --------------------------------------------------------------------------- #


@router.get("/{autobiography_id}/customization/options", response_model=CustomizationOptionsResponse)
async def get_customization_options(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> CustomizationOptionsResponse:
    """사용 가능한 말투(10)·구성(5)·컨셉(9) 선택지 전체 목록을 반환한다."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    return CustomizationOptionsResponse(
        tones=[
            CustomizationOptionItem(key=k, name=v["name"], description=v["description"], example=v.get("example"))
            for k, v in prompts.TONE_OPTIONS.items()
        ],
        structures=[
            CustomizationOptionItem(key=k, name=v["name"], description=v["description"], example=v.get("example"))
            for k, v in prompts.STRUCTURE_OPTIONS.items()
        ],
        concepts=[
            CustomizationOptionItem(key=k, name=v["name"], description=v["description"], example=v.get("example"))
            for k, v in prompts.CONCEPT_OPTIONS.items()
        ],
    )


@router.get(
    "/{autobiography_id}/customization/recommendations",
    response_model=CustomizationRecommendationResponse,
)
async def get_customization_recommendations(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> CustomizationRecommendationResponse:
    """이 유저가 실제로 답변한 고정 질문들의 태그(app/data/question_bank.py의
    suggested_tags)를 집계해 말투·구성·컨셉 추천 조합을 반환한다. 참고용
    힌트일 뿐이라 select 단계에서 다른 조합을 자유롭게 골라도 무방하다."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    recommendations = await autobiography_service.get_customization_recommendations(
        gateways, autobiography_id
    )
    return CustomizationRecommendationResponse(**recommendations)


@router.post("/{autobiography_id}/customization/select", response_model=AutobiographyRead)
async def select_customization(
    autobiography_id: uuid.UUID,
    payload: CustomizationSelectionRequest,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> AutobiographyRead:
    """말투·구성·컨셉 각 2개를 선택해 저장한다."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    try:
        autobiography = await autobiography_service.save_customization_selection(
            gateways, autobiography_id,
            tones=payload.tones, structures=payload.structures, concepts=payload.concepts,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return AutobiographyRead.model_validate(autobiography)


@router.post("/{autobiography_id}/customization/previews", status_code=status.HTTP_202_ACCEPTED)
async def generate_previews(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> dict:
    """8개 샘플 미리보기 생성 트리거. 8회의 LLM 호출이 필요하므로 Celery에 위임한다."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    from app.workers.tasks import generate_sample_previews as preview_task

    preview_task.delay(str(autobiography_id))
    return {"detail": "Sample previews generation queued"}


@router.get("/{autobiography_id}/customization/previews", response_model=SamplePreviewsResponse)
async def get_previews(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> SamplePreviewsResponse:
    """생성된 8개 샘플 미리보기를 조회한다. 아직 생성 전이면 빈 배열."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    previews = await autobiography_service.get_sample_previews(gateways, autobiography_id)
    if previews is None:
        return SamplePreviewsResponse(samples=[])
    return SamplePreviewsResponse(
        samples=[SamplePreviewItem(**preview) for preview in previews]
    )


@router.post("/{autobiography_id}/customization/confirm", response_model=AutobiographyRead)
async def confirm_customization(
    autobiography_id: uuid.UUID,
    payload: CustomizationConfirmRequest,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> AutobiographyRead:
    """8개 샘플 중 마음에 드는 조합을 최종 확정한다."""
    await _require_own_autobiography(gateways, autobiography_id, current_user)
    try:
        autobiography = await autobiography_service.confirm_customization(
            gateways, autobiography_id,
            tone=payload.tone, structure=payload.structure, concept=payload.concept,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return AutobiographyRead.model_validate(autobiography)


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
    autobiography = await _require_own_autobiography(gateways, autobiography_id, current_user)
    chapter = await autobiography_service.get_chapter_draft(gateways, chapter_draft_id)
    if chapter is None or chapter.autobiography_id != autobiography_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "해당 자서전에 속한 챕터를 찾을 수 없습니다.")
    if not autobiography.book_synopsis:
        # 이전엔 이 선행 조건 미충족이 Celery 태스크 안에서만 ValueError로 죽어
        # 클라이언트는 202를 받고도 아무 일도 일어나지 않는 것처럼 보였다
        # (2026-07-16 해소 — 알려진 한계 "202 실패가 HTTP로 전달 안 됨").
        raise HTTPException(
            status.HTTP_409_CONFLICT, "먼저 목차를 선택해야 챕터를 집필할 수 있습니다."
        )

    from app.workers.tasks import write_chapter as write_chapter_task

    write_chapter_task.delay(str(chapter_draft_id))
    return {"detail": "Chapter writing queued"}


@router.patch("/{autobiography_id}/chapters/{chapter_draft_id}/content", response_model=AutobiographyRead)
async def update_chapter_content(
    autobiography_id: uuid.UUID,
    chapter_draft_id: uuid.UUID,
    payload: ChapterContentUpdate,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> AutobiographyRead:
    """완성된 자서전의 챕터 본문을 사용자가 직접 고쳐 저장한다("나의 자서전" 직접
    수정 — AI 재집필(POST .../write)과는 별개 기능으로 둘 다 계속 쓸 수 있다).

    이 요청은 LLM/외부 API를 전혀 호출하지 않는 즉시 완료되는 단순 텍스트
    저장이다(autobiography_service.edit_chapter_content 참조) — 세션 대화 저장
    경로에서 예전에 실제로 겪었던 "느린 외부 호출을 기다리며 DB 트랜잭션을
    오래 열어둬 Supabase가 idle 커넥션을 끊어버리는" 문제(interview_service.
    add_user_turn 모듈 docstring)가 애초에 발생할 여지가 없도록 설계했다."""
    autobiography = await _require_own_autobiography(gateways, autobiography_id, current_user)
    chapter = await autobiography_service.get_chapter_draft(gateways, chapter_draft_id)
    if chapter is None or chapter.autobiography_id != autobiography_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "해당 자서전에 속한 챕터를 찾을 수 없습니다.")
    if not autobiography.final_content:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "최종본이 완성된 뒤에만 직접 수정할 수 있습니다."
        )

    updated = await autobiography_service.edit_chapter_content(
        gateways, autobiography_id, chapter_draft_id, payload.content
    )
    return AutobiographyRead.model_validate(updated)


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
    chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography_id)
    if not chapters or any(chapter.content is None for chapter in chapters):
        # 선행 조건(전 챕터 집필 완료) 미충족을 202 뒤 워커 내부 실패로 숨기지 않고
        # 즉시 알려준다(2026-07-16 해소 — 알려진 한계 "202 실패가 HTTP로 전달 안 됨").
        raise HTTPException(
            status.HTTP_409_CONFLICT, "모든 챕터의 집필이 끝난 뒤에 최종본을 만들 수 있습니다."
        )
    from app.workers.tasks import finalize_manuscript as finalize_task

    finalize_task.delay(str(autobiography_id))
    return {"detail": "Manuscript finalization queued"}


@router.put("/{autobiography_id}/photo-placements", response_model=AutobiographyRead)
async def set_photo_placements(
    autobiography_id: uuid.UUID,
    payload: PhotoPlacementsUpdate,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> AutobiographyRead:
    """PDF 조판 직전, 자서전에 수록할 사진과 배치(고정 슬롯)를 통째로 교체 저장한다.
    PUT 시맨틱 — 보낸 배열이 전체 상태다(빈 배열 = 수록 사진 없음으로 확정, 자동
    선택 폴백도 하지 않음). pdf/generate가 이 값을 읽어 조판에 반영한다."""
    autobiography = await _require_own_autobiography(gateways, autobiography_id, current_user)
    try:
        updated = await autobiography_service.set_photo_placements(
            gateways,
            autobiography,
            [item.model_dump(mode="json") for item in payload.placements],
        )
    except autobiography_service.InvalidPhotoPlacementError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return AutobiographyRead.model_validate(updated)


@router.post("/{autobiography_id}/pdf/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_pdf(
    autobiography_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> dict:
    """실물 출판용 국판(A5) PDF 조판 트리거. 선행 조건(final_content, 최종 윤문
    완료)은 여기서 즉시 409로 알려준다(2026-07-16 해소 — 이전엔 워커 안에서만
    ValueError로 실패해 클라이언트가 202를 받고도 폴링으로 간접 확인해야 했다).
    워커 쪽 검증(pdf_service.generate_manuscript_pdf)은 큐잉 이후 상태 변화에
    대비한 이중 방어로 그대로 둔다."""
    autobiography = await _require_own_autobiography(gateways, autobiography_id, current_user)
    if not autobiography.final_content:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "최종본(윤문)이 완성된 뒤에 PDF를 만들 수 있습니다."
        )
    from app.workers.tasks import generate_manuscript_pdf as generate_pdf_task

    generate_pdf_task.delay(str(autobiography_id))
    return {"detail": "Manuscript PDF generation queued"}


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
