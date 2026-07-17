"""자서전 수록 사진 배치(autobiography_service.set_photo_placements +
pdf_service._placements_by_chapter) 테스트.

핵심 계약(2026-07-16, PDF 조판 전 사진·위치 선택): 배치는 기획안 5절의 고정 슬롯
원칙에 따라 {media_asset_id, chapter_index, slot, caption}으로만 표현되고, 본인
소유 이미지·실존 챕터만 가리킬 수 있다. 조판은 사용자가 지정한 사진만 수록한다 —
미지정(None)이든 빈 배열이든 사진 없이 조판되며, 자동 선택은 없다(2026-07-17에
기존 자동 선택 폴백 제거).
"""

from __future__ import annotations

import uuid

import pytest

from app.gateways.dto import ChapterDraftCreateData, MediaAssetCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import AssetType
from app.services import autobiography_service, pdf_service
from app.services.autobiography_service import InvalidPhotoPlacementError


async def _setup(gateways):
    user = await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )
    autobiography = await gateways.autobiographies.create(user.id)
    await gateways.chapters.replace_all(
        autobiography.id,
        [ChapterDraftCreateData(chapter_index=1, title="유년"), ChapterDraftCreateData(chapter_index=2, title="청년")],
    )
    photo = await gateways.media_assets.create(
        MediaAssetCreateData(
            user_id=user.id, s3_key="k", s3_url="https://example.com/k.jpg",
            asset_type=AssetType.IMAGE,
        )
    )
    await gateways.commit()
    return user, autobiography, photo


@pytest.mark.asyncio
async def test_set_photo_placements_persists_valid_placement() -> None:
    gateways = _build_mock_gateways()
    _, autobiography, photo = await _setup(gateways)

    updated = await autobiography_service.set_photo_placements(
        gateways,
        autobiography,
        [{"media_asset_id": str(photo.id), "chapter_index": 2, "slot": "full_page_before", "caption": "결혼식"}],
    )

    assert updated.photo_placements == [
        {"media_asset_id": str(photo.id), "chapter_index": 2, "slot": "full_page_before", "caption": "결혼식"}
    ]


@pytest.mark.asyncio
async def test_set_photo_placements_rejects_foreign_or_missing_media() -> None:
    gateways = _build_mock_gateways()
    _, autobiography, _ = await _setup(gateways)

    with pytest.raises(InvalidPhotoPlacementError):
        await autobiography_service.set_photo_placements(
            gateways,
            autobiography,
            [{"media_asset_id": str(uuid.uuid4()), "chapter_index": 1, "slot": "chapter_top", "caption": None}],
        )


@pytest.mark.asyncio
async def test_set_photo_placements_rejects_unknown_chapter() -> None:
    gateways = _build_mock_gateways()
    _, autobiography, photo = await _setup(gateways)

    with pytest.raises(InvalidPhotoPlacementError):
        await autobiography_service.set_photo_placements(
            gateways,
            autobiography,
            [{"media_asset_id": str(photo.id), "chapter_index": 99, "slot": "chapter_top", "caption": None}],
        )


@pytest.mark.asyncio
async def test_empty_placements_mean_no_photos_not_fallback() -> None:
    """빈 배열은 "수록 사진 없음" 확정으로 그대로 저장된다."""
    gateways = _build_mock_gateways()
    _, autobiography, _ = await _setup(gateways)

    updated = await autobiography_service.set_photo_placements(gateways, autobiography, [])

    assert updated.photo_placements == []


def test_placements_by_chapter_groups_slots_and_skips_missing_urls() -> None:
    photo_id, gone_id = uuid.uuid4(), uuid.uuid4()
    url_by_id = {photo_id: "https://example.com/a.jpg"}
    placements = [
        {"media_asset_id": str(photo_id), "chapter_index": 1, "slot": "chapter_top", "caption": "봄"},
        {"media_asset_id": str(photo_id), "chapter_index": 2, "slot": "full_page_before", "caption": None},
        {"media_asset_id": str(gone_id), "chapter_index": 1, "slot": "chapter_top", "caption": None},
    ]

    grouped = pdf_service._placements_by_chapter(placements, url_by_id)

    assert grouped[1]["top_photos"] == [{"url": "https://example.com/a.jpg", "caption": "봄"}]
    assert grouped[1]["full_page_photos"] == []
    assert grouped[2]["full_page_photos"] == [{"url": "https://example.com/a.jpg", "caption": None}]
