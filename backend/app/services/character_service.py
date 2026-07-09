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
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import prompts
from app.clients import solar
from app.models import (
    Autobiography,
    ChapterDraft,
    Character,
    CharacterMention,
    ConsentRecord,
    ConsentType,
    RiskClassification,
)


async def scan_and_classify_chapter(
    db: AsyncSession, *, chapter: ChapterDraft, autobiography: Autobiography
) -> list[Character]:
    if not chapter.content:
        return []

    extraction = await solar.structured_completion(
        prompts.build_ner_extraction_prompt(chapter_content=chapter.content),
        schema_name="ner_extraction",
        json_schema=prompts.NER_EXTRACTION_SCHEMA,
        reasoning_effort="low",
    )

    characters: list[Character] = []
    for person in extraction.get("people", []):
        name = person["name"].strip()
        if not name:
            continue
        character = await _get_or_create_character(
            db,
            autobiography_id=autobiography.id,
            real_name=name,
            relation=person.get("relation_to_narrator"),
        )
        risk = await _classify_risk(person_name=name, chapter_excerpt=chapter.content)
        if risk.get("risk_detected"):
            character.risk_classification = RiskClassification(risk["risk_classification"])
        db.add(CharacterMention(character_id=character.id, chapter_draft_id=chapter.id))
        characters.append(character)

    await db.flush()
    return characters


async def _get_or_create_character(
    db: AsyncSession, *, autobiography_id: uuid.UUID, real_name: str, relation: str | None
) -> Character:
    result = await db.execute(
        select(Character).where(
            Character.autobiography_id == autobiography_id, Character.real_name == real_name
        )
    )
    character = result.scalar_one_or_none()
    if character is not None:
        return character

    count_result = await db.execute(
        select(func.count()).select_from(Character).where(Character.autobiography_id == autobiography_id)
    )
    next_index = count_result.scalar_one() + 1
    display_name = relation or f"지인 {next_index}"

    character = Character(
        autobiography_id=autobiography_id,
        display_name=display_name,
        real_name=real_name,
        relation_to_user=relation,
    )
    db.add(character)
    await db.flush()
    return character


async def _classify_risk(*, person_name: str, chapter_excerpt: str) -> dict:
    return await solar.structured_completion(
        prompts.build_third_party_risk_prompt(person_name=person_name, chapter_excerpts=[chapter_excerpt]),
        schema_name="third_party_risk",
        json_schema=prompts.THIRD_PARTY_RISK_SCHEMA,
        reasoning_effort="low",
    )


async def list_characters(db: AsyncSession, autobiography_id: uuid.UUID) -> list[Character]:
    result = await db.execute(
        select(Character).where(Character.autobiography_id == autobiography_id).order_by(Character.created_at)
    )
    return list(result.scalars().all())


async def get_character(db: AsyncSession, character_id: uuid.UUID) -> Character | None:
    return await db.get(Character, character_id)


async def retain_real_name(
    db: AsyncSession, character_id: uuid.UUID, *, notice_version: str
) -> Character:
    """
    실명 유지 opt-in. 전수 가명화 기본값을 뒤집는 유일한 경로이므로, 인물 단위 법적
    책임 고지에 대한 유효한 동의(ConsentRecord)가 선행되어야 한다.

    한계: 현재 ConsentRecord는 user_id + consent_type 단위로만 기록되고 인물 단위로
    세분화되어 있지 않다(기획안이 요구하는 "인물 단위" 동의를 완전히 구현하려면
    consent_records.character_id 같은 FK 추가가 필요 — 별도 스키마 변경 대상이며
    이번 서비스 레이어 작업 범위 밖이다). 따라서 여기서는 "이 사용자가 실명 유지
    고지에 최소 1회 동의했는가"라는 완화된 게이트로 동작한다.
    """
    character = await db.get(Character, character_id)
    if character is None:
        raise ValueError(f"Character {character_id} not found")

    autobiography = await db.get(Autobiography, character.autobiography_id)
    if autobiography is None:
        raise ValueError(f"Autobiography {character.autobiography_id} not found")

    result = await db.execute(
        select(ConsentRecord)
        .where(
            ConsentRecord.user_id == autobiography.user_id,
            ConsentRecord.consent_type == ConsentType.DISCLOSURE_REALNAME,
            ConsentRecord.revoked_at.is_(None),
        )
        .order_by(ConsentRecord.granted_at.desc())
    )
    if result.scalars().first() is None:
        raise PermissionError(
            f"인물 '{character.display_name}' 실명 유지 전 DISCLOSURE_REALNAME 동의가 필요합니다."
        )

    character.real_name_retained = True
    character.disclosure_notice_version = notice_version
    character.disclosure_acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(character)
    return character
