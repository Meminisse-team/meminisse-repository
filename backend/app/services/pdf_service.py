"""
Phase 5: 실물 출판 파이프라인 — Jinja2로 최종 원고를 고정 슬롯 템플릿에 주입하고
WeasyPrint(CSS Paged Media)로 국판(A5) PDF를 조판한다(기획안 "실물 책 출판을 위한
자동 조판 파이프라인 설계" 절 참조). POD(주문형 인쇄) 발주 연계는 범위 밖 — 완성된
PDF를 S3에 올려 URL을 Autobiography.pdf_url에 저장하는 데까지만 담당한다.

WeasyPrint는 Windows에서 순수 pip 설치만으로는 동작하지 않는다 — pango/gobject 등
네이티브 라이브러리가 필요해 GTK3 런타임을 별도로 설치해야 한다(2026-07-12 실제
설치·검증: `winget install tschoonj.GTKForWindows`, 설치 후 `C:\Program Files\
GTK3-Runtime Win64\bin`이 PATH에 있어야 import 자체가 성공한다).
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from functools import lru_cache
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.gateways.dto import AutobiographyRecord
from app.gateways.factory import Gateways
from app.models.enums import AssetType
from app.services import autobiography_service

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_MANUSCRIPT_FONT_FAMILY = "Gowun Batang"
_MANUSCRIPT_FONT_CSS_URL = "https://fonts.googleapis.com/css2?family=Gowun+Batang&display=swap"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "jinja"]),
)


@lru_cache(maxsize=1)
def _resolve_manuscript_font_url() -> str:
    """Google Fonts CSS2 API 응답에서 실제 woff2 URL을 추출한다. gstatic URL은
    폰트 버전이 갱신될 때마다 바뀌므로 하드코딩하지 않고 매번(프로세스당 1회,
    lru_cache) 해석한다. 실패해도 예외를 던지지 않는다 — 빈 문자열을 반환하면
    @font-face의 src가 깨진 채로 무시되고 body의 `serif` 폴백으로 자연스럽게
    넘어간다(WeasyPrint는 로컬에 한글 세리프가 없으면 시스템 폰트로 대체한다)."""
    try:
        response = httpx.get(
            _MANUSCRIPT_FONT_CSS_URL,
            # UA 없이 요청하면 Google이 구형 브라우저용 eot/ttf만 내려줘 woff2를 못 찾는다.
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        match = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", response.text)
        return match.group(1) if match else ""
    except Exception:
        logger.warning(
            "Google Fonts에서 %s 폰트 URL을 가져오지 못했습니다 — 시스템 세리프로 대체합니다.",
            _MANUSCRIPT_FONT_FAMILY,
            exc_info=True,
        )
        return ""


def _split_paragraphs(content: str) -> list[str]:
    """LLM이 생성한 본문은 문단을 빈 줄로 구분한다고 가정한다. 빈 줄 구분이 전혀
    없는 경우(단일 블록)에도 최소 1개 문단으로는 렌더링되도록 폴백한다."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    if parts:
        return parts
    stripped = content.strip()
    return [stripped] if stripped else []


async def _first_photo_url_for_chapter(
    gateways: Gateways, source_event_ids: list[uuid.UUID], media_url_by_id: dict[uuid.UUID, str]
) -> str | None:
    """챕터가 소환한 이벤트 중 사진이 딸린 첫 번째 것을 그 챕터의 대표 사진으로
    쓴다(기획안의 "상단 이미지+하단 캡션형" 슬롯에 대응). 여러 장을 배치하는
    레이아웃은 MVP 범위 밖으로 남긴다."""
    if not source_event_ids:
        return None
    events = await gateways.events.list_by_ids(source_event_ids)
    for event in events:
        if event.media_asset_id and event.media_asset_id in media_url_by_id:
            return media_url_by_id[event.media_asset_id]
    return None


async def render_manuscript_html(gateways: Gateways, autobiography: AutobiographyRecord) -> str:
    chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    media_assets = await gateways.media_assets.list_by_user(autobiography.user_id)
    media_url_by_id = {m.id: m.s3_url for m in media_assets if m.asset_type == AssetType.IMAGE}

    chapter_views = []
    for chapter in chapters:
        photo_url = await _first_photo_url_for_chapter(
            gateways, chapter.source_event_ids, media_url_by_id
        )
        part_context = autobiography_service.get_chapter_part_context(autobiography, chapter.chapter_index)
        chapter_views.append(
            {
                "chapter_index": chapter.chapter_index,
                "title": chapter.title,
                "paragraphs": _split_paragraphs(chapter.content or ""),
                "photo_url": photo_url,
                "part_index": part_context["part_index"] if part_context else None,
                "part_title": part_context["part_title"] if part_context else None,
                "is_part_opening": bool(part_context and part_context["is_part_opening"]),
            }
        )

    parts = autobiography_service.get_ordered_parts(autobiography)

    template = _jinja_env.get_template("manuscript.html.jinja")
    return template.render(
        title=autobiography.title,
        book_synopsis=autobiography.book_synopsis,
        chapters=chapter_views,
        parts=parts,
        font_url=_resolve_manuscript_font_url(),
    )


def _run_pdf_qa(pdf_bytes: bytes, *, expected_min_pages: int) -> dict:
    """조판 직후 최소 유효성 검증(기획안 "조판 QA" 절): 페이지 수, 폰트 임베딩 여부.

    실제 텍스트 오버플로우(문단이 페이지 밖으로 밀려나는지) 검출은 WeasyPrint의
    공개 API만으로는 신뢰성 있게 할 수 없다 — 내부 박스 트리를 직접 순회해야 하는데
    버전 호환성이 약한 사설 API라 여기서는 하지 않는다. 대신 "챕터 수 + 표지/목차에
    비해 페이지 수가 비정상적으로 적다"는 대리 신호로 렌더링 실패(CSS 깨짐 등)를
    간접적으로 잡는다."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    fonts_embedded = False
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        font_dict = resources.get("/Font") or {}
        for font_obj in font_dict.values():
            font = font_obj.get_object()
            # 한글처럼 문자 수가 많은 폰트는 WeasyPrint가 Type0(CID) 합성 폰트로
            # 내보낸다 — 그 경우 FontDescriptor는 최상위가 아니라
            # DescendantFonts[0] 안에 있다(실제 생성된 PDF에서 재현·확인,
            # 2026-07-12 — 처음엔 최상위만 봐서 실제로는 임베딩된 폰트를 "없음"
            # 으로 오판했다).
            if font.get("/Subtype") == "/Type0":
                descendants = font.get("/DescendantFonts") or []
                descriptor = (
                    descendants[0].get_object().get("/FontDescriptor") if descendants else None
                )
            else:
                descriptor = font.get("/FontDescriptor")
            if descriptor and any(
                key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")
            ):
                fonts_embedded = True
                break
        if fonts_embedded:
            break
    return {
        "page_count": page_count,
        "fonts_embedded": fonts_embedded,
        "meets_expected_min_pages": page_count >= expected_min_pages,
    }


async def generate_manuscript_pdf(gateways: Gateways, autobiography_id: uuid.UUID) -> AutobiographyRecord:
    autobiography = await gateways.autobiographies.get_by_id(autobiography_id)
    if autobiography is None:
        raise ValueError(f"autobiography not found: {autobiography_id}")
    if not autobiography.final_content:
        raise ValueError("최종 윤문(finalize_manuscript)이 끝난 뒤에 PDF를 만들 수 있습니다.")

    html_content = await render_manuscript_html(gateways, autobiography)
    pdf_bytes = HTML(string=html_content, base_url=".").write_pdf()

    chapters = await gateways.chapters.list_by_autobiography(autobiography.id)
    ordered_parts = autobiography_service.get_ordered_parts(autobiography)
    # +2: 표지, 목차. Part 구조가 있으면 Part마다 구분 페이지가 1장씩 추가된다.
    qa_report = _run_pdf_qa(pdf_bytes, expected_min_pages=len(chapters) + 2 + len(ordered_parts))
    if not qa_report["meets_expected_min_pages"] or not qa_report["fonts_embedded"]:
        logger.warning(
            "PDF QA 경고(autobiography_id=%s): %s — 그래도 업로드는 계속 진행하고 관리자가"
            " 사후 확인하도록 한다(조판 실패로 사용자에게 결과물이 아예 안 가는 것보다는 낫다).",
            autobiography_id,
            qa_report,
        )

    key = f"users/{autobiography.user_id}/manuscripts/{autobiography.id}.pdf"
    pdf_url = await gateways.storage.put_object(key, pdf_bytes, content_type="application/pdf")

    autobiography = await gateways.autobiographies.update(autobiography_id, pdf_url=pdf_url)
    await gateways.commit()
    return autobiography
