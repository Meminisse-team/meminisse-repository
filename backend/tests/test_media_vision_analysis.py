"""
Azure Vision(물체 탐지·장면 태그 + 사진 속 텍스트 인식) 기반 사진 분석 파이프라인
회귀 테스트(media_service._run_dual_track_analysis, app/clients/azure_vision.py).

이전에는 Azure Vision의 Caption 기능(자연어 한 문장 요약)을 썼는데, 일부 지역
에서만 지원되고 그마저도 영어로만 생성되는 제약을 실제 배포 중 반복 재현했다
(2026-07-16). 물체 탐지(objects)+장면 태그(tags)는 이런 제약이 없어 대체
채택했고, 그 결과(영어 키워드)를 Solar로 한국어 명사구 하나로 다듬어
image_caption 컬럼에 저장한다(media_service._describe_scene).

핵심 계약:
- image_caption(물체/태그를 Solar로 다듬은 한국어 설명)은 사진 속 텍스트 유무와
  무관하게 항상 저장된다(물체/태그가 하나라도 감지됐다면).
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

from app.agents import prompts
from app.clients import azure_vision
from app.gateways.dto import MediaAssetCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import AssetType, EventSourceType, MediaAnalysisTrack
from app.services import media_service


def _fake_analysis(
    *, objects: list[str] | None = None, tags: list[str] | None = None,
    read_lines: list[str] | None = None,
) -> dict:
    return {
        "objectsResult": {
            "values": [{"tags": [{"name": o, "confidence": 0.9}]} for o in (objects or [])]
        },
        "tagsResult": {
            "values": [{"name": t, "confidence": 0.9} for t in (tags or [])]
        },
        "readResult": {"blocks": [{"lines": [{"text": line} for line in read_lines]}]}
        if read_lines
        else {"blocks": []},
    }


def _fake_analyze_image(analysis: dict):
    """analyze_image는 await되므로, 정적인 dict가 아니라 그 dict를 반환하는
    코루틴 함수로 패치해야 한다."""

    async def _analyze_image(image_bytes: bytes, **kwargs) -> dict:
        return analysis

    return _analyze_image


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def _fake_chat_completion(description: str):
    """media_service._describe_scene이 쓰는 solar.chat_completion을 대체한다 —
    objects/tags(영어)를 한국어 명사구로 다듬는 단계를 고정된 문자열로 대신한다."""

    async def _chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion(description)

    return _chat_completion


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
    """글자가 없는 순수 추억 사진도 물체/태그 기반 설명은 항상 얻어야 한다 —
    Document Parse 시절엔 이 경우 아무 단서도 저장되지 않았던 것과 대비되는
    핵심 개선."""
    analysis = _fake_analysis(objects=["person"], tags=["outdoor", "grass"], read_lines=None)
    with (
        patch("app.clients.azure_vision.analyze_image", new=_fake_analyze_image(analysis)),
        patch(
            "app.clients.solar.chat_completion",
            new=_fake_chat_completion("실외에서 사람이 잔디 위에 서 있는 사진"),
        ),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_passages", new=_fake_embeddings),
    ):
        gateways = _build_mock_gateways()
        _, asset = await _create_user_and_asset(gateways)

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.image_caption == "실외에서 사람이 잔디 위에 서 있는 사진"
        assert updated.image_ocr_text is None
        assert updated.analysis_track == MediaAnalysisTrack.PURE_MEMORY
        assert updated.pre_extracted_labels == analysis

        # 텍스트가 없으므로 Event(DOCUMENT) 스테이징도 없어야 한다.
        events = await gateways.events.list_unmerged_verified(asset.user_id)
        assert events == []


@pytest.mark.asyncio
async def test_sufficient_text_sets_text_document_track_and_stages_event() -> None:
    long_text = "1990년 집 앞에서 가족들과 찍은 사진이다." + "." * 10
    analysis = _fake_analysis(
        objects=["person", "person"], tags=["outdoor"], read_lines=[long_text]
    )
    with (
        patch("app.clients.azure_vision.analyze_image", new=_fake_analyze_image(analysis)),
        patch(
            "app.clients.solar.chat_completion",
            new=_fake_chat_completion("집 앞에서 여러 사람이 서 있는 사진"),
        ),
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
    analysis = _fake_analysis(tags=["landscape"], read_lines=["짧음"])
    with (
        patch("app.clients.azure_vision.analyze_image", new=_fake_analyze_image(analysis)),
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion("풍경 사진")),
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
async def test_no_objects_or_tags_detected_skips_solar_call_and_leaves_caption_none() -> None:
    """물체도 태그도 하나도 감지되지 않으면 불필요한 Solar 호출 없이 바로
    image_caption을 None으로 남긴다(media_service._describe_scene 참조)."""
    analysis = _fake_analysis(read_lines=None)

    async def _fail_if_called(messages, **kwargs):
        raise AssertionError("objects/tags가 없으면 chat_completion을 호출하면 안 된다")

    with (
        patch("app.clients.azure_vision.analyze_image", new=_fake_analyze_image(analysis)),
        patch("app.clients.solar.chat_completion", new=_fail_if_called),
        patch("app.clients.embeddings.embed_passages", new=_fake_embeddings),
    ):
        gateways = _build_mock_gateways()
        _, asset = await _create_user_and_asset(gateways)

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.image_caption is None


@pytest.mark.asyncio
async def test_azure_not_configured_skips_analysis_without_crashing() -> None:
    """AZURE_CV_ENDPOINT/AZURE_CV_API_KEY가 비어 있으면(로컬 개발 기본값) 분석을
    건너뛰어야 한다 — 앱이 죽지 않고, 나중에 키만 채우면 다음 업로드부터 동작한다."""

    async def _raise_not_configured(image_bytes, **kwargs):
        raise azure_vision.AzureVisionNotConfiguredError("not configured")

    with patch("app.clients.azure_vision.analyze_image", new=_raise_not_configured):
        gateways = _build_mock_gateways()
        _, asset = await _create_user_and_asset(gateways)

        await media_service._run_dual_track_analysis(gateways, asset=asset, file_bytes=b"x")

        updated = await gateways.media_assets.get_by_id(asset.id)
        assert updated.analysis_track is None
        assert updated.image_caption is None
        assert updated.image_ocr_text is None


def test_extract_objects_preserves_duplicate_counts() -> None:
    """같은 이름이 여러 번 감지돼도(예: 사람 2명) 중복 제거하면 안 된다 —
    실사진(아이 두 명이 있는 사진) 검증 중, 이름만 남기고 중복 제거했더니 최종
    한국어 설명이 "아이" 한 명으로 뭉개지는 문제를 재현했다(2026-07-16)."""
    analysis = {
        "objectsResult": {
            "values": [
                {"boundingBox": {}, "tags": [{"name": "person", "confidence": 0.86}]},
                {"boundingBox": {}, "tags": [{"name": "person", "confidence": 0.53}]},
            ]
        }
    }
    assert azure_vision.extract_objects(analysis) == ["person", "person"]


def test_build_scene_description_prompt_annotates_repeated_object_counts() -> None:
    """감지된 사물이 반복되면 "이름×횟수" 표기로 LLM에 개수를 명시적으로
    알려준다 — 그래야 "아이 두 명"처럼 인원수를 반영한 한국어 설명이 나온다."""
    messages = prompts.build_scene_description_prompt(
        objects=["person", "person"], tags=["boy", "girl"]
    )
    user_content = messages[1]["content"]
    assert "person×2" in user_content
    assert "boy" in user_content and "girl" in user_content
