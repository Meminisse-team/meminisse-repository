"""
Azure Vision(캡션 + 사진 속 텍스트 인식) 기반 사진 분석 파이프라인 회귀 테스트
(media_service._run_dual_track_analysis, app/clients/azure_vision.py).

이전에는 Upstage Document Parse(텍스트만 읽는 OCR)를 썼는데, 글자가 없는 순수
추억 사진에서는 아무 단서도 얻지 못했다. Azure Vision은 캡션(사진의 시각적 내용을
설명하는 문장)과 텍스트 인식(read)을 한 번의 호출로 함께 지원하므로, 텍스트가
있든 없든 캡션은 항상 얻는다.

핵심 계약:
- 캡션(image_caption)은 사진 속 텍스트 유무와 무관하게 항상 저장된다.
- 사진 속 텍스트가 충분히 길면(>=20자) TEXT_DOCUMENT 트랙 + Event(DOCUMENT) 스테이징,
  아니면(없거나 너무 짧으면) PURE_MEMORY 트랙 + Event 생성 없음.
- Azure Vision이 설정돼 있지 않으면(AzureVisionNotConfiguredError) 분석 자체를
  건너뛴다 — 업로드/워커가 죽지 않고, analysis_track/caption 모두 null로 남는다.
- Event(DOCUMENT)는 더 이상 "대기 중 확인" 상태로 특별 취급되지 않는다 — 생성 시점의
  검증 게이트(_check_ocr_validity)만 거치고, 이후 정리(삭제)는 Phase 3 중복 병합에
  맡긴다(interview_service.add_user_turn에는 더 이상 이 정리 로직이 없다).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.clients import azure_vision
from app.gateways.dto import MediaAssetCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import AssetType, EventSourceType, MediaAnalysisTrack
from app.services import media_service


def _fake_analysis(*, caption: str | None, read_lines: list[str] | None) -> dict:
    return {
        "captionResult": {"text": caption} if caption else {},
        "readResult": {"blocks": [{"lines": [{"text": line} for line in read_lines]}]}
        if read_lines
        else {"blocks": []},
    }


def _fake_analyze_image(analysis: dict):
    """analyze_image는 await되므로, 정적인 dict가 아니라 그 dict를 반환하는
    코루틴 함수로 패치해야 한다."""

    async def _analyze_image(image_bytes: bytes, language: str = "ko") -> dict:
        return analysis

    return _analyze_image


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    if schema_name == "ocr_validity":
        return {"suspicious": False, "note": "이상 없음"}
    if schema_name == "ocr_date_extraction":
        return {"found": False, "extracted_year": None, "extracted_age": None}
    raise AssertionError(f"unexpected schema_name: {schema_name}")


async def _fake_embeddings(texts: list[str]) -> list[list[float]]:
    return [[0.0] for _ in texts]


async def _create_user_and_asset(gateways):
    user = await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )
    asset = await gateways.media_assets.create(
        MediaAssetCreateData(
            user_id=user.id, s3_key="k", s3_url="https://example.com/k", asset_type=AssetType.IMAGE,
        )
    )
    await gateways.commit()
    return user, asset


@pytest.mark.asyncio
async def test_caption_is_stored_even_when_no_text_detected() -> None:
    """글자가 없는 순수 추억 사진도 캡션은 항상 얻어야 한다 — Document Parse
    시절엔 이 경우 아무 단서도 저장되지 않았던 것과 대비되는 핵심 개선."""
    analysis = _fake_analysis(caption="집 앞에서 5명이 함께 찍은 사진", read_lines=None)
    with (
        patch("app.clients.azure_vision.analyze_image", new=_fake_analyze_image(analysis)),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_passages", new=_fake_embeddings),
    ):
        gateways = _build_mock_gateways()
        _, asset = await _create_user_and_asset(gateways)

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.image_caption == "집 앞에서 5명이 함께 찍은 사진"
        assert updated.image_ocr_text is None
        assert updated.analysis_track == MediaAnalysisTrack.PURE_MEMORY
        assert updated.pre_extracted_labels == analysis

        # 텍스트가 없으므로 Event(DOCUMENT) 스테이징도 없어야 한다.
        events = await gateways.events.list_unmerged_verified(asset.user_id)
        assert events == []


@pytest.mark.asyncio
async def test_sufficient_text_sets_text_document_track_and_stages_event() -> None:
    long_text = "1990년 집 앞에서 가족들과 찍은 사진이다." + "." * 10
    analysis = _fake_analysis(caption="집 앞에서 여러 사람이 서 있는 사진", read_lines=[long_text])
    with (
        patch("app.clients.azure_vision.analyze_image", new=_fake_analyze_image(analysis)),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_passages", new=_fake_embeddings),
    ):
        gateways = _build_mock_gateways()
        _, asset = await _create_user_and_asset(gateways)

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.analysis_track == MediaAnalysisTrack.TEXT_DOCUMENT
        assert updated.image_caption == "집 앞에서 여러 사람이 서 있는 사진"
        assert updated.image_ocr_text == long_text

        events = await gateways.events.list_unmerged_verified(asset.user_id)
        assert len(events) == 1
        assert events[0].source_type == EventSourceType.DOCUMENT
        assert events[0].media_asset_id == asset.id
        assert events[0].verified is True


@pytest.mark.asyncio
async def test_short_text_is_treated_as_no_text() -> None:
    """너무 짧은 텍스트(노이즈로 간주)는 image_ocr_text에 저장되지 않고, 트랙도
    PURE_MEMORY로 남는다."""
    analysis = _fake_analysis(caption="풍경 사진", read_lines=["짧음"])
    with (
        patch("app.clients.azure_vision.analyze_image", new=_fake_analyze_image(analysis)),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_passages", new=_fake_embeddings),
    ):
        gateways = _build_mock_gateways()
        _, asset = await _create_user_and_asset(gateways)

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.analysis_track == MediaAnalysisTrack.PURE_MEMORY
        assert updated.image_ocr_text is None
        assert updated.image_caption == "풍경 사진"


@pytest.mark.asyncio
async def test_azure_not_configured_skips_analysis_without_crashing() -> None:
    """AZURE_CV_ENDPOINT/AZURE_CV_API_KEY가 비어 있으면(로컬 개발 기본값) 분석을
    건너뛰어야 한다 — 앱이 죽지 않고, 나중에 키만 채우면 다음 업로드부터 동작한다."""

    async def _raise_not_configured(image_bytes, language="ko"):
        raise azure_vision.AzureVisionNotConfiguredError("not configured")

    with patch("app.clients.azure_vision.analyze_image", new=_raise_not_configured):
        gateways = _build_mock_gateways()
        _, asset = await _create_user_and_asset(gateways)

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.analysis_track is None
        assert updated.image_caption is None
        assert updated.image_ocr_text is None
