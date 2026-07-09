"""
등장인물 검토(기획안 Phase 4, 6절 법적 리스크 관리): NER 스캔으로 챕터 본문에서
구술자 외 제3자 인물을 찾아 Character 레코드로 승격하고, 서술 성격(위해성)을
분류해 고지 등급 신호를 저장한다.

이 분류는 가명 적용 여부를 결정하는 게이트가 아니다 — Character.real_name_retained의
기본값은 항상 false이며(전수 가명화 opt-out), 실명 유지는 인물 단위 법적 책임 고지
동의(ConsentRecord)를 확인한 뒤에만 retain_real_name()을 통해 true로 전환된다.

별도 로컬 NER 모델이 아직 연동되지 않아 Solar Structured Outputs로 인물 후보를
스캔한다 — 탐지 재현율의 한계가 있으므로, 자동 탐지에서 누락된 인물은 최종 검토
화면에서 사용자가 직접 추가 지정할 수 있어야 한다(이 모듈의 책임 밖, 프론트엔드/API
레이어의 향후 작업).
"""

from __future__ import annotations

import uuid

from app.agents import prompts
from app.clients import solar
from app.gateways.dto import AutobiographyRecord, ChapterDraftRecord, CharacterCreateData, CharacterRecord
from app.gateways.factory import Gateways
from app.models.enums import ConsentType, RiskClassification


async def scan_and_classify_chapter(
    gateways: Gateways, *, chapter: ChapterDraftRecord, autobiography: AutobiographyRecord
) -> list[CharacterRecord]:
    """write_chapter() 파이프라인의 마지막 단계로 호출된다 — 커밋은 호출부(write_chapter)
    책임이므로 여기서는 하지 않는다."""
    if not chapter.content:
        return []

    extraction = await solar.structured_completion(
        prompts.build_ner_extraction_prompt(chapter_content=chapter.content),
        schema_name="ner_extraction",
        json_schema=prompts.NER_EXTRACTION_SCHEMA,
        reasoning_effort="low",
    )

    characters: list[CharacterRecord] = []
    for person in extraction.get("people", []):
        name = person["name"].strip()
        if not name:
            continue
        character = await gateways.characters.get_or_create(
            CharacterCreateData(
                autobiography_id=autobiography.id,
                real_name=name,
                relation_to_user=person.get("relation_to_narrator"),
            )
        )
        risk = await _classify_risk(person_name=name, chapter_excerpt=chapter.content)
        if risk.get("risk_detected"):
            await gateways.characters.update_risk_classification(
                character.id, RiskClassification(risk["risk_classification"])
            )
        await gateways.characters.add_mention(character.id, chapter_draft_id=chapter.id)
        characters.append(character)

    return characters


async def _classify_risk(*, person_name: str, chapter_excerpt: str) -> dict:
    return await solar.structured_completion(
        prompts.build_third_party_risk_prompt(person_name=person_name, chapter_excerpts=[chapter_excerpt]),
        schema_name="third_party_risk",
        json_schema=prompts.THIRD_PARTY_RISK_SCHEMA,
        reasoning_effort="low",
    )


async def list_characters(gateways: Gateways, autobiography_id: uuid.UUID) -> list[CharacterRecord]:
    return await gateways.characters.list_by_autobiography(autobiography_id)


async def get_character(gateways: Gateways, character_id: uuid.UUID) -> CharacterRecord | None:
    return await gateways.characters.get(character_id)


async def retain_real_name(
    gateways: Gateways, character_id: uuid.UUID, *, notice_version: str
) -> CharacterRecord:
    """
    실명 유지 opt-in. 전수 가명화 기본값을 뒤집는 유일한 경로이므로, 인물 단위 법적
    책임 고지에 대한 유효한 동의(ConsentRecord)가 선행되어야 한다.

    한계: 현재 ConsentRecord는 user_id + consent_type 단위로만 기록되고 인물 단위로
    세분화되어 있지 않다(기획안이 요구하는 "인물 단위" 동의를 완전히 구현하려면
    consent_records.character_id 같은 FK 추가가 필요 — 별도 스키마 변경 대상이며
    이번 서비스 레이어 작업 범위 밖이다). 따라서 여기서는 "이 사용자가 실명 유지
    고지에 최소 1회 동의했는가"라는 완화된 게이트로 동작한다.
    """
    character = await gateways.characters.get(character_id)
    if character is None:
        raise ValueError(f"Character {character_id} not found")

    autobiography = await gateways.autobiographies.get_by_id(character.autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {character.autobiography_id} not found")

    has_consent = await gateways.consents.has_active(autobiography.user_id, ConsentType.DISCLOSURE_REALNAME)
    if not has_consent:
        raise PermissionError(
            f"인물 '{character.display_name}' 실명 유지 전 DISCLOSURE_REALNAME 동의가 필요합니다."
        )

    character = await gateways.characters.retain_real_name(character_id, notice_version=notice_version)
    await gateways.commit()
    return character
