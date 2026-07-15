"""
사진 속 텍스트 기반 시기(life_period_mapped) 자동 추정 회귀 테스트
(docs/QUESTION_BANK_GUIDE.md 5절, media_service._guess_life_period_from_ocr_text).

Azure Vision(app/clients/azure_vision.py)이 사진 속에서 읽어낸 텍스트를 대상으로
한다 — 어떤 엔진이 텍스트를 추출했는지와 무관하게 시기 추정 로직 자체는 동일하다.

핵심 규칙:
- 텍스트에 명시적 연도가 있고 사용자 birth_year가 있으면 나이를 역산해 생애주기를 매핑한다.
- 텍스트에 명시적 나이가 있으면 그걸로 바로 매핑한다.
- 단서가 전혀 없으면(found=false) life_period_mapped는 None으로 남는다.
- 사용자가 이미 age_at_time으로 life_period_mapped를 채워둔 사진은 추정 자체를 건너뛴다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.gateways.dto import MediaAssetCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import AssetType, LifePeriod
from app.services import media_service

# _MIN_TEXT_LENGTH_FOR_DOCUMENT_TRACK(20자)보다 길어야 TEXT_DOCUMENT 트랙으로 분류된다.
_READ_TEXT = "일기장에 적힌 글귀. 이 사진과 관련된 내용이 여기 담겨 있다." + "." * 10


def _make_analyze_image(*, read_text: str | None):
    async def _analyze_image(image_bytes: bytes, language: str = "ko") -> dict:
        return {
            "captionResult": {"text": "사진 속 풍경", "confidence": 0.9},
            "readResult": {"blocks": [{"lines": [{"text": read_text}]}] if read_text else []},
        }

    return _analyze_image


def _patches(*, structured_completion, read_text: str | None = _READ_TEXT):
    return (
        patch("app.clients.azure_vision.analyze_image", new=_make_analyze_image(read_text=read_text)),
        patch("app.clients.solar.structured_completion", new=structured_completion),
        patch(
            "app.clients.embeddings.embed_passages",
            new=lambda texts: _fake_embeddings(texts),
        ),
    )


async def _fake_embeddings(texts: list[str]) -> list[list[float]]:
    return [[0.0] for _ in texts]


def _make_structured_completion(*, ocr_found: bool, extracted_year=None, extracted_age=None):
    async def _structured_completion(messages, *, schema_name, json_schema, **kwargs):
        if schema_name == "ocr_validity":
            return {"suspicious": False, "note": "이상 없음"}
        if schema_name == "ocr_date_extraction":
            return {
                "found": ocr_found,
                "extracted_year": extracted_year,
                "extracted_age": extracted_age,
            }
        raise AssertionError(f"unexpected schema_name: {schema_name}")

    return _structured_completion


async def _create_user(gateways, *, birth_year=None):
    return await gateways.users.create(
        UserCreateData(
            id=uuid.uuid4(),
            email=f"{uuid.uuid4()}@test.local",
            name="테스터",
            birth_year=birth_year,
        )
    )


async def _create_asset(gateways, user_id, *, life_period_mapped=None):
    return await gateways.media_assets.create(
        MediaAssetCreateData(
            user_id=user_id,
            s3_key="k",
            s3_url="https://example.com/k",
            asset_type=AssetType.IMAGE,
            life_period_mapped=life_period_mapped,
        )
    )


@pytest.mark.asyncio
async def test_explicit_year_with_known_birth_year_maps_life_period() -> None:
    p1, p2, p3 = _patches(
        structured_completion=_make_structured_completion(ocr_found=True, extracted_year=1975)
    )
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _create_user(gateways, birth_year=1956)  # 1975년에 19살
        asset = await _create_asset(gateways, user.id)
        await gateways.commit()

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.life_period_mapped == LifePeriod.YOUTH


@pytest.mark.asyncio
async def test_explicit_age_maps_life_period_directly() -> None:
    p1, p2, p3 = _patches(
        structured_completion=_make_structured_completion(ocr_found=True, extracted_age=20)
    )
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _create_user(gateways)
        asset = await _create_asset(gateways, user.id)
        await gateways.commit()

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.life_period_mapped == LifePeriod.ADULTHOOD


@pytest.mark.asyncio
async def test_no_clues_leaves_life_period_unmapped() -> None:
    p1, p2, p3 = _patches(structured_completion=_make_structured_completion(ocr_found=False))
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _create_user(gateways, birth_year=1956)
        asset = await _create_asset(gateways, user.id)
        await gateways.commit()

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.life_period_mapped is None


@pytest.mark.asyncio
async def test_already_mapped_via_user_input_skips_ocr_estimation() -> None:
    """extracted_age가 다른 값이어도, 사용자가 이미 age_at_time으로 매핑해둔
    사진은 OCR 추정 결과에 덮어써지지 않는다."""
    p1, p2, p3 = _patches(
        structured_completion=_make_structured_completion(ocr_found=True, extracted_age=5)
    )
    with p1, p2, p3:
        gateways = _build_mock_gateways()
        user = await _create_user(gateways)
        asset = await _create_asset(gateways, user.id, life_period_mapped=LifePeriod.SENIOR)
        await gateways.commit()

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.life_period_mapped == LifePeriod.SENIOR
