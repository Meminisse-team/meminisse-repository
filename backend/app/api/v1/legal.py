from fastapi import APIRouter

from app.agents import prompts
from app.schemas.legal import DisclosuresRead

router = APIRouter(prefix="/legal", tags=["legal"])


@router.get("/disclosures", response_model=DisclosuresRead)
async def get_disclosures() -> DisclosuresRead:
    """3층 고지(기획안 4절): 비의료 서비스임을 온보딩/약관에 명시하기 위한 정적 문구.
    인증 불필요 — 온보딩 동의 화면(가입 전)에서부터 노출돼야 하기 때문이다."""
    return DisclosuresRead(non_medical_service=prompts.NON_MEDICAL_SERVICE_DISCLOSURE)
