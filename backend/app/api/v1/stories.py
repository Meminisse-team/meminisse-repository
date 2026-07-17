"""
'나의 이야기' 탭 — 세션 단위 카드 조회 전용 라우터. app/api/v1/events.py(사건 단위
조회)와 관심사가 달라 별도 파일로 뒀다 — story_service.py 모듈 docstring 참조.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUserDep, GatewaysDep
from app.schemas.story import StoryCardPageRead, StoryCardRead, StoryProseUpdate
from app.services import story_service

router = APIRouter(prefix="/stories", tags=["stories"])


@router.get("", response_model=StoryCardPageRead)
async def list_stories(
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
    limit: int = Query(7, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> StoryCardPageRead:
    """'나의 이야기' 탭 목록 — limit/offset으로 실제 DB 레벨 페이지네이션을
    적용한다(예전엔 이 엔드포인트가 완료된 세션 전체를 매번 내려주고 프론트가
    화면에 보일 7개만 잘라내는 방식이라, 세션이 많을수록 페이지를 넘겨도
    체감 속도가 똑같았다 — story_service.list_story_cards 참조)."""
    page = await story_service.list_story_cards(
        gateways, current_user.id, limit=limit, offset=offset
    )
    return StoryCardPageRead(
        items=[StoryCardRead.model_validate(card) for card in page.items],
        total=page.total,
    )


@router.patch("/{session_id}", response_model=StoryCardRead)
async def update_story_prose(
    session_id: uuid.UUID,
    payload: StoryProseUpdate,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> StoryCardRead:
    """재조립된 산문이 마음에 들지 않을 때 사용자가 직접 고쳐 저장한다. 저장할 때만
    호출되고(프론트가 타이핑마다 호출하지 않음), 저장 즉시 이 세션의 이벤트를
    새 텍스트로 재추출하므로 최종 원고에도 반영된다."""
    try:
        card = await story_service.update_session_prose(
            gateways, current_user.id, session_id, payload.prose
        )
    except story_service.StoryNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "이야기를 찾을 수 없습니다.")
    except story_service.ProseNotReadyError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "아직 정리되지 않은 이야기예요. 잠시 후 다시 시도해주세요."
        )
    except story_service.ProseEditCooldownError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"{exc.retry_after_seconds}초 후 다시 시도해주세요.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    return StoryCardRead.model_validate(card)
