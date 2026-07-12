"""
Phase 3/4 오케스트레이션(팀원의 "DB" 브랜치 작업)이 Gateway 패턴 위에서 실제로
끝까지 도는지 검증하는 회귀 테스트.

이 브랜치의 배경: `feature/architecture-setup` 브랜치가 main에 먼저 병합된 뒤,
Gateway 패턴 PR이 실수로 그 브랜치에만 병합되고 main에는 반영되지 못한 상태에서
브랜치가 삭제되었다. 그 사이 팀원이 main 위에 Phase 3/4(이벤트 병합·중요도 산정·
동적 목차·하향식 집필·등장인물 검토·동의 기록)를 예전 방식(AsyncSession 직접 주입)
으로 구현해 병합했다. 이 테스트는 그 팀원 작업분을 Gateway 패턴으로 옮겨 붙인 뒤
전체 파이프라인이 깨지지 않았음을 Mock 백엔드로 증명한다.

Solar 호출은 전부 모킹한다 — 이 테스트의 목적은 프롬프트 품질이 아니라 "여러
서비스와 게이트웨이를 넘나드는 배선이 실제로 끝까지 연결되어 있는가"이다.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.gateways.dto import EventCreateData, SessionCreateData
from app.gateways.factory import Gateways, _build_mock_gateways
from app.models.enums import ConsentGrantedBy, ConsentType, EventSourceType, SessionType
from app.schemas.user import UserCreate
from app.services import autobiography_service, character_service, consent_service, user_service


async def _fake_admin_create_user(*, email: str, password: str, user_metadata: dict) -> uuid.UUID:
    """user_service.create_user가 이제 Supabase Auth Admin API를 호출한다
    (app/clients/supabase_auth.py) — 이 테스트의 관심사는 그 외부 호출이 아니라
    Gateway 배선이므로, 다른 Upstage 호출들과 동일하게 모킹한다."""
    return uuid.uuid4()


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str, model: str = "solar-pro3") -> None:
        self.choices = [_FakeChoice(content)]
        self.model = model


async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
    return _FakeCompletion("김철수와 함께한 소중한 순간이었다.")


_STRUCTURED_RESPONSES = {
    "event_merge_judge": {"same_event": False, "reasoning": "다른 사건으로 판단"},
    "toc_generation": {
        "candidates": [
            {"chapters": [{"chapter_index": 1, "title": "1장. 어린 시절", "theme_keywords": ["어린시절"]}]},
            {"chapters": [{"chapter_index": 1, "title": "1장. 대안", "theme_keywords": ["대안"]}]},
        ]
    },
    "book_title": {"title": "부산의 여름"},
    "fact_reextraction": {"facts": [{"fact_type": "person", "raw_text": "김철수"}]},
    "ner_extraction": {"people": [{"name": "김철수", "relation_to_narrator": "친구"}]},
    "third_party_risk": {
        "person_name": "김철수", "risk_detected": False, "risk_classification": "none", "risk_reasons": [],
    },
}


async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
    return _STRUCTURED_RESPONSES[schema_name]


async def _fake_classify_entailment(*, premise: str, hypothesis: str) -> dict[str, float]:
    """실제 NLI 모델(app/clients/nli.py)은 무겁고(torch/transformers) 이 테스트의
    관심사(파이프라인 배선)와 무관하므로 모킹한다 — 항상 entailment로 고정해
    groundedness_report에 플래그가 남지 않게 한다."""
    return {"entailment": 0.9, "neutral": 0.08, "contradiction": 0.02}


async def _seed_user_with_events(gateways: Gateways):
    user = await user_service.create_user(
        gateways, UserCreate(email="p34@example.com", name="테스터", password="test-password-123")
    )

    session = await gateways.sessions.create(
        SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
    )
    await gateways.sessions.set_session_prose(session.id, "나는 부산에서 태어나 자랐다.")
    await gateways.sessions.complete(session.id)

    events = await gateways.events.bulk_create(
        [
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT,
                one_line_summary="부산 출생", prose_paragraph="나는 부산에서 태어났다.",
                verified=True, emotion_intensity=3,
            ),
            EventCreateData(
                user_id=user.id, source_type=EventSourceType.SESSION_CHAT,
                one_line_summary="김철수와의 우정", prose_paragraph="김철수와 함께 학교를 다녔다.",
                verified=True, emotion_intensity=4,
            ),
        ]
    )
    await gateways.events.bulk_update_embeddings(
        [(events[0].id, [1.0, 0.0, 0.0]), (events[1].id, [0.0, 1.0, 0.0])]
    )
    await gateways.commit()
    return user


@pytest.mark.asyncio
async def test_full_phase3_4_pipeline_runs_end_to_end() -> None:
    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
        patch("app.clients.nli.classify_entailment", new=_fake_classify_entailment),
    ):
        gateways = _build_mock_gateways()
        user = await _seed_user_with_events(gateways)

        # Phase 3: 병합 판정(모두 별개 사건 처리) + 중요도 산정 + 스타일 바이블
        autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)
        assert autobiography.status.value == "consolidated"
        assert autobiography.style_bible is not None

        # Phase 4: 목차 후보 생성 → 선택(챕터 초안 + 책 시놉시스) → 챕터 집필 → 최종 윤문
        autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography.id)
        assert len(autobiography.toc_data["candidates"]) == 2

        autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)
        assert autobiography.book_synopsis
        assert autobiography.title == "부산의 여름"

        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
        assert len(chapters) == 1

        chapter = await autobiography_service.write_chapter(gateways, chapters[0].id)
        assert chapter.content
        assert chapter.factcheck_report is not None
        assert chapter.status.value == "reviewed"

        # write_chapter가 끝에 등장인물 스캔(character_service)까지 트리거해야 한다.
        characters = await character_service.list_characters(gateways, autobiography.id)
        assert [c.real_name for c in characters] == ["김철수"]
        assert characters[0].real_name_retained is False  # 전수 가명화 기본값(opt-out)

        autobiography = await autobiography_service.finalize_manuscript(gateways, autobiography.id)
        assert autobiography.final_content

        finalized_chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
        assert all(c.status.value == "finalized" for c in finalized_chapters)


@pytest.mark.asyncio
async def test_retain_real_name_requires_disclosure_consent() -> None:
    """전수 가명화 opt-out 게이트: 동의 없이는 실명 유지가 절대 허용되면 안 된다."""
    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
        patch("app.clients.nli.classify_entailment", new=_fake_classify_entailment),
    ):
        gateways = _build_mock_gateways()
        user = await _seed_user_with_events(gateways)

        autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)
        autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography.id)
        autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)
        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
        await autobiography_service.write_chapter(gateways, chapters[0].id)

        characters = await character_service.list_characters(gateways, autobiography.id)
        character = characters[0]

        with pytest.raises(PermissionError):
            await character_service.retain_real_name(gateways, character.id, notice_version="v1")

        # 인물 단위 동의(character_id 미지정)는 이 인물을 풀어주지 않는다 — 같은
        # 자서전의 다른 인물에 대한 동의로 이 인물의 실명까지 유지되는 걸 막는 게
        # consent_records.character_id 세분화의 목적이므로(character_service.
        # retain_real_name 주석 참조), 사용자 단위로만 동의를 남기면 여전히 막혀야 한다.
        await consent_service.record_consent(
            gateways,
            user.id,
            consent_type=ConsentType.DISCLOSURE_REALNAME,
            notice_version="v1",
            granted_by=ConsentGrantedBy.SELF,
        )
        with pytest.raises(PermissionError):
            await character_service.retain_real_name(gateways, character.id, notice_version="v1")

        await consent_service.record_consent(
            gateways,
            user.id,
            consent_type=ConsentType.DISCLOSURE_REALNAME,
            notice_version="v1",
            granted_by=ConsentGrantedBy.SELF,
            character_id=character.id,
        )
        character = await character_service.retain_real_name(gateways, character.id, notice_version="v1")
        assert character.real_name_retained is True
