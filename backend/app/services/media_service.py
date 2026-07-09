"""
Phase 1: 기록물 대량 스캔 — 업로드 즉시 S3(Layer 0)에 원본을 보존하고, 듀얼 트랙으로
분기한다. 텍스트가 유의미하게 검출되면 TEXT_DOCUMENT 트랙(Document Parse → Solar
1차 타당성 검증 → 미검증 Event 스테이징), 아니면 PURE_MEMORY 트랙(유저 코멘트만 저장).

TEXT_DOCUMENT 트랙에서 생성되는 Event는 verified=false로 스테이징되며, 해당 생애주기
인터뷰 시점에 확인 질문(prompts.build_ocr_confirmation_question)으로 제시해 유저가
확인해야 verified=true로 승격된다 — 그 인터뷰 턴 연동은 interview_service의 향후
작업(TODO)이며, 이 서비스는 스테이징까지만 책임진다.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import prompts
from app.clients import document_parse, s3, solar
from app.clients import embeddings as embeddings_client
from app.models import AssetType, Event, EventSourceType, LifePeriod, MediaAnalysisTrack, MediaAsset
from app.schemas.media import MediaAssetCreate

# 이 길이 미만이면 "텍스트가 사실상 없는 사진"으로 간주해 PURE_MEMORY 트랙으로 분류한다.
_MIN_TEXT_LENGTH_FOR_DOCUMENT_TRACK = 20


def map_age_to_life_period(age: int | None) -> LifePeriod | None:
    if age is None:
        return None
    if age < 13:
        return LifePeriod.CHILDHOOD
    if age < 20:
        return LifePeriod.YOUTH
    if age < 60:
        return LifePeriod.ADULTHOOD
    return LifePeriod.SENIOR


async def upload_media_asset(
    db: AsyncSession,
    payload: MediaAssetCreate,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> MediaAsset:
    s3_key = f"users/{payload.user_id}/media/{uuid.uuid4()}_{filename}"
    s3_url = await s3.upload_bytes(s3_key, file_bytes, content_type=content_type)

    asset = MediaAsset(
        user_id=payload.user_id,
        session_id=payload.session_id,
        s3_key=s3_key,
        s3_url=s3_url,
        asset_type=payload.asset_type,
        age_at_time=payload.age_at_time,
        location_at_time=payload.location_at_time,
        people_at_time=payload.people_at_time,
        life_period_mapped=map_age_to_life_period(payload.age_at_time),
        user_comment=payload.user_comment,
    )
    db.add(asset)
    await db.flush()

    if payload.asset_type == AssetType.IMAGE:
        await _run_dual_track_analysis(db, asset=asset, file_bytes=file_bytes, filename=filename)

    await db.commit()
    await db.refresh(asset)
    return asset


async def _run_dual_track_analysis(
    db: AsyncSession, *, asset: MediaAsset, file_bytes: bytes, filename: str
) -> None:
    parsed = await document_parse.parse_document_sync(file_bytes, filename, output_formats=["text"])
    extracted_text = (parsed.get("content") or {}).get("text", "").strip()

    if len(extracted_text) < _MIN_TEXT_LENGTH_FOR_DOCUMENT_TRACK:
        asset.analysis_track = MediaAnalysisTrack.PURE_MEMORY
        return

    asset.analysis_track = MediaAnalysisTrack.TEXT_DOCUMENT
    asset.pre_extracted_labels = parsed

    validity = await _check_ocr_validity(extracted_text)
    event = Event(
        user_id=asset.user_id,
        source_type=EventSourceType.DOCUMENT,
        media_asset_id=asset.id,
        life_period=asset.life_period_mapped,
        one_line_summary=extracted_text[:100],
        prose_paragraph=extracted_text,
        source_span={"quoted_text": extracted_text[:200]},
        confidence={"ocr_validity_note": validity["note"]},
        verified=not validity["suspicious"],
    )
    db.add(event)
    await db.flush()

    if event.verified:
        vectors = await embeddings_client.embed_passages([event.prose_paragraph])
        event.embedding = vectors[0]


async def _check_ocr_validity(extracted_text: str) -> dict:
    return await solar.structured_completion(
        prompts.build_ocr_validity_check_prompt(ocr_text=extracted_text),
        schema_name="ocr_validity",
        json_schema=prompts.OCR_VALIDITY_CHECK_SCHEMA,
        reasoning_effort="low",
    )
