"""
Phase 5(실물 출판) 회귀 테스트: Phase 3/4 파이프라인을 끝까지 돌려 final_content를
만든 뒤, pdf_service.generate_manuscript_pdf가 실제로 유효한 PDF를 만들어 Mock
오브젝트 스토리지에 올리고 Autobiography.pdf_url을 채우는지 검증한다.

WeasyPrint 자체는 모킹하지 않는다 — 이 프로젝트에서 실제로 GTK3 런타임을 설치해
동작을 확인했으므로(2026-07-12), 여기서도 실제 렌더링을 태워 결과 PDF가 유효한지
pypdf로 직접 검사한다. 네트워크가 필요한 부분(Solar, 임베딩, NLI, Google Fonts)만
모킹한다.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from pypdf import PdfReader

from app.gateways.factory import _build_mock_gateways
from app.services import autobiography_service, pdf_service
from tests.test_autobiography_phase34_pipeline import (
    _fake_admin_create_user,
    _fake_chat_completion,
    _fake_classify_entailment,
    _fake_structured_completion,
    _seed_user_with_events,
)


@pytest.mark.asyncio
async def test_generate_manuscript_pdf_produces_valid_pdf_and_stores_url() -> None:
    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.embeddings.embed_query", return_value=[1.0, 0.0, 0.0]),
        patch("app.clients.supabase_auth.admin_create_user", new=_fake_admin_create_user),
        patch("app.clients.nli.classify_entailment", new=_fake_classify_entailment),
        patch("app.services.pdf_service._resolve_manuscript_font_url", return_value=""),
    ):
        gateways = _build_mock_gateways()
        user = await _seed_user_with_events(gateways)

        autobiography = await autobiography_service.consolidate_autobiography(gateways, user.id)
        autobiography = await autobiography_service.generate_toc_candidates(gateways, autobiography.id)
        autobiography = await autobiography_service.select_toc_candidate(gateways, autobiography.id, 0)
        chapters = await autobiography_service.list_chapter_drafts(gateways, autobiography.id)
        await autobiography_service.write_chapter(gateways, chapters[0].id)
        autobiography = await autobiography_service.finalize_manuscript(gateways, autobiography.id)
        assert autobiography.final_content

        autobiography = await pdf_service.generate_manuscript_pdf(gateways, autobiography.id)

        assert autobiography.pdf_url is not None
        assert autobiography.pdf_url.startswith("mock://objects/")

        pdf_bytes = gateways.storage._store.objects[  # type: ignore[attr-defined]
            f"users/{user.id}/manuscripts/{autobiography.id}.pdf"
        ]
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # 표지 + 목차 + 챕터 1개 = 최소 3페이지.
        assert len(reader.pages) >= 3
        extracted = "".join(page.extract_text() or "" for page in reader.pages)
        assert "1장" in extracted


@pytest.mark.asyncio
async def test_generate_manuscript_pdf_rejects_before_finalize() -> None:
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

        with pytest.raises(ValueError, match="최종 윤문"):
            await pdf_service.generate_manuscript_pdf(gateways, autobiography.id)
