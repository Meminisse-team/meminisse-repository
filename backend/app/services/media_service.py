"""
Phase 1: 기록물 대량 스캔 — 업로드 즉시 S3(Layer 0)에 원본을 보존하고, Azure Vision
(Image Analysis, app/clients/azure_vision.py)으로 물체 탐지·장면 태그와 사진 속
텍스트를 함께 얻는다. 물체/태그(영어)는 Solar로 한국어 명사구 하나로 다듬어
(_describe_scene, 예: "실외에서 사람과 집이 보이는 사진") 텍스트 유무와 무관하게
항상 PHOTO 세션 오프닝 질문의 재료가 된다(prompts.build_photo_session_opening).
사진 속에 텍스트(손글씨 메모 등)까지 검출되면 TEXT_DOCUMENT 트랙으로 분류해 Solar
1차 타당성 검증을 거친 뒤 Event(source_type=DOCUMENT)로도 스테이징한다 — 대화가
아직 일어나지 않아도 그 자체로 검색 가능한 사실이 되고, 실제로 그 사진에 대해
나눈 대화에서 비슷한 사실이 다시 추출되면 Phase 3 중복 병합(_merge_duplicate_events)
이 자연스럽게 흡수한다(별도의 "대기 중 확인" 상태나 삭제 로직을 두지 않는다).
텍스트가 없으면(PURE_MEMORY 트랙) 물체/태그 설명에만 의존한다.

Azure Vision의 Caption 기능(자연어 한 문장 요약)은 쓰지 않는다 — 일부 지역에서만
지원되고 그마저도 영어로만 생성돼(app/clients/azure_vision.py 모듈 docstring
참조) 실제 배포 중 여러 지역에서 반복 실패를 겪었다(2026-07-16). 물체 탐지(objects)
+장면 태그(tags)는 이런 제약이 없어 대체 채택했다.

예전에는 Upstage Document Parse(텍스트만 읽는 OCR)를 썼는데, 글자가 없는 순수
추억 사진에서는 아무 단서도 얻지 못했다 — Azure Vision으로 교체했다(2026-07-15).

듀얼 트랙 분석(_run_dual_track_analysis)은 외부 동기 API 호출을 포함하는데, 예전에
이걸 업로드 요청 안에서 그대로 await했더니 사진 한 장 올릴 때마다 응답이 몇십 초~수
분씩 걸리고, 외부 API가 일시적으로 오류를 내면 업로드 자체가 500으로 실패했다 —
"사진을 올려도 처리가 끝나지 않는" 것처럼 보이는 버그였다(2026-07-12 재현). PDF
생성·자서전 집필과 동일하게 Celery 워커로 위임해, 업로드 요청은 S3 저장 + DB row
생성까지만 하고 즉시 응답하도록 바꿨다(app/workers/tasks.py의 analyze_media_asset
참조).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.agents import prompts
from app.clients import azure_vision, solar
from app.clients import embeddings as embeddings_client
from app.gateways.dto import EventCreateData, MediaAssetCreateData, MediaAssetRecord
from app.gateways.factory import Gateways
from app.models.enums import AssetType, EventSourceType, LifePeriod, MediaAnalysisTrack
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
    gateways: Gateways,
    payload: MediaAssetCreate,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> MediaAssetRecord:
    s3_key = f"users/{payload.user_id}/media/{uuid.uuid4()}_{filename}"
    s3_url = await gateways.storage.put_object(s3_key, file_bytes, content_type=content_type)

    asset = await gateways.media_assets.create(
        MediaAssetCreateData(
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
    )
    await gateways.commit()

    if payload.asset_type == AssetType.IMAGE:
        # 세션 커밋이 이미 끝난 뒤에 큐잉한다 — 브로커(Redis)가 잠깐 응답하지 않아도
        # 업로드 자체는 사용자에게 성공으로 보여야 한다(interview_service.complete_session
        # 의 동일한 패턴 참조). delay()는 브로커에 동기적으로 연결을 시도하는 블로킹
        # 호출이라 asyncio.to_thread로 이벤트 루프 밖에서 돌린다.
        from app.workers.tasks import analyze_media_asset  # 순환 임포트 방지용 지연 임포트

        try:
            await asyncio.to_thread(analyze_media_asset.delay, str(asset.id))
        except Exception:
            logging.getLogger(__name__).warning(
                "analyze_media_asset 큐잉 실패 (media_asset_id=%s) — 업로드 자체는 "
                "이미 완료됐으나 듀얼 트랙 분석이 예약되지 못했다.",
                asset.id,
                exc_info=True,
            )

    return asset


async def analyze_media_asset(gateways: Gateways, media_asset_id: uuid.UUID) -> None:
    """Celery 워커 전용 진입점(app/workers/tasks.py의 analyze_media_asset 태스크).
    업로드 요청 때 받았던 file_bytes를 브로커 메시지에 그대로 실어 보내지 않고,
    S3에 이미 저장된 원본을 s3_key로 다시 내려받아 분석한다."""
    asset = await gateways.media_assets.get_by_id(media_asset_id)
    if asset is None:
        raise KeyError(f"media asset not found: {media_asset_id}")
    file_bytes = await gateways.storage.get_object(asset.s3_key)
    await _run_dual_track_analysis(gateways, asset=asset, file_bytes=file_bytes)
    await gateways.commit()


async def _run_dual_track_analysis(
    gateways: Gateways, *, asset: MediaAssetRecord, file_bytes: bytes
) -> None:
    try:
        analysis = await azure_vision.analyze_image(file_bytes)
    except azure_vision.AzureVisionNotConfiguredError:
        logging.getLogger(__name__).info(
            "Azure Computer Vision 키가 설정되지 않아 사진 %s 분석을 건너뜁니다 "
            "(.env에 AZURE_CV_ENDPOINT/AZURE_CV_API_KEY를 채우면 다음 업로드부터 "
            "별도 코드 수정 없이 자동으로 동작합니다).",
            asset.id,
        )
        return

    objects = azure_vision.extract_objects(analysis)
    tags = azure_vision.extract_tags(analysis)
    caption = await _describe_scene(objects=objects, tags=tags)
    read_text = azure_vision.extract_read_text(analysis)
    has_text = bool(read_text) and len(read_text) >= _MIN_TEXT_LENGTH_FOR_DOCUMENT_TRACK

    life_period_mapped = asset.life_period_mapped
    if life_period_mapped is None and has_text:
        life_period_mapped = await _guess_life_period_from_ocr_text(
            gateways, user_id=asset.user_id, extracted_text=read_text
        )

    await gateways.media_assets.update_analysis(
        asset.id,
        analysis_track=MediaAnalysisTrack.TEXT_DOCUMENT if has_text else MediaAnalysisTrack.PURE_MEMORY,
        pre_extracted_labels=analysis,
        life_period_mapped=life_period_mapped,
        image_caption=caption,
        image_ocr_text=read_text if has_text else None,
    )

    if not has_text:
        return

    validity = await _check_ocr_validity(read_text)
    verified = not validity["suspicious"]
    event = await gateways.events.create(
        EventCreateData(
            user_id=asset.user_id,
            source_type=EventSourceType.DOCUMENT,
            media_asset_id=asset.id,
            life_period=life_period_mapped,
            one_line_summary=read_text[:100],
            prose_paragraph=read_text,
            source_span={"quoted_text": read_text[:200]},
            confidence={"ocr_validity_note": validity["note"]},
            verified=verified,
        )
    )

    if verified:
        vectors = await embeddings_client.embed_passages([event.prose_paragraph])
        await gateways.events.bulk_update_embeddings([(event.id, vectors[0])])


async def _describe_scene(*, objects: list[str], tags: list[str]) -> str | None:
    """objects/tags(영어 키워드)를 Solar로 한국어 명사구 하나로 다듬는다
    (prompts.build_scene_description_prompt) — Azure Vision의 Caption 기능을
    대체한다(지역·언어 제약 때문에 objects/tags로 전환, 2026-07-16). 아무것도
    감지되지 않았으면 불필요한 Solar 호출 없이 바로 None을 반환한다.

    reasoning_effort="low"에서는 입력이 영어 키워드다 보니 종종 그대로 영어로
    답해버리는 실패가 실사용 검증 중 재현됐다(4회 중 1회). "medium"으로 올리니
    같은 검증에서 안정적으로 한국어 결과가 나왔다."""
    if not objects and not tags:
        return None
    response = await solar.chat_completion(
        prompts.build_scene_description_prompt(objects=objects, tags=tags),
        reasoning_effort="medium",
    )
    description = (response.choices[0].message.content or "").strip()
    return description or None


async def _guess_life_period_from_ocr_text(
    gateways: Gateways, *, user_id: uuid.UUID, extracted_text: str
) -> LifePeriod | None:
    """OCR 텍스트에 명시적인 연도나 나이가 있으면 그걸로 생애주기를 추정한다.
    애매한 문맥 추측은 하지 않는다(prompts.OCR_DATE_EXTRACTION_SYSTEM_PROMPT
    참조) — 잘못 매핑하면 사진(PHOTO) 세션 오케스트레이션이 엉뚱한 생애주기
    경계에서 사진을 들이밀 수 있으므로, 확신 없으면 None(시기 불명으로 남겨
    전체 완료 후 몰아보기에서 다루게 한다)이 더 안전하다."""
    result = await solar.structured_completion(
        prompts.build_ocr_date_extraction_prompt(ocr_text=extracted_text),
        schema_name="ocr_date_extraction",
        json_schema=prompts.OCR_DATE_EXTRACTION_SCHEMA,
        reasoning_effort="low",
    )
    if not result.get("found"):
        return None

    age = result.get("extracted_age")
    if age is None:
        extracted_year = result.get("extracted_year")
        if extracted_year is None:
            return None
        user = await gateways.users.get_by_id(user_id)
        if user is None or user.birth_year is None:
            return None
        age = extracted_year - user.birth_year

    if age is None or age < 0:
        return None
    return map_age_to_life_period(age)


async def list_media_assets(gateways: Gateways, user_id: uuid.UUID) -> list[MediaAssetRecord]:
    """GET /media-assets(사진첩 탭). created_at 내림차순 — 최근 업로드가 먼저 온다."""
    return await gateways.media_assets.list_by_user(user_id)


async def get_media_asset(gateways: Gateways, media_asset_id: uuid.UUID) -> MediaAssetRecord | None:
    """GET /media-assets/{id} — PHOTO 세션 채팅 화면이 linked_media_asset_id로 사진
    원본(s3_url)을 조회할 때 쓴다(목록 전체를 내려받아 클라이언트에서 찾을 필요 없이)."""
    return await gateways.media_assets.get_by_id(media_asset_id)


async def _check_ocr_validity(extracted_text: str) -> dict:
    return await solar.structured_completion(
        prompts.build_ocr_validity_check_prompt(ocr_text=extracted_text),
        schema_name="ocr_validity",
        json_schema=prompts.OCR_VALIDITY_CHECK_SCHEMA,
        reasoning_effort="low",
    )
