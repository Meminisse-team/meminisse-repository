"""Repository domain operations."""
from __future__ import annotations







import uuid








from app.gateways.dto import AutobiographyRecord, AutobiographyStatusRecord, ChapterDraftRecord, ChapterStatusRecord

from app.gateways.factory import Gateways

from app.models.enums import AssetType



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
