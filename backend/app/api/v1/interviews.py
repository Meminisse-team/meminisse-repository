import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, GatewaysDep
from app.gateways.dto import InterviewSessionRecord, UserRecord
from app.schemas.interview import ChatMessageCreate, ChatMessageRead, SessionCreate, SessionRead, TurnResponse
from app.services import interview_service

router = APIRouter(prefix="/interview-sessions", tags=["interviews"])


async def _get_own_session_or_404(
    gateways: GatewaysDep, session_id: uuid.UUID, current_user: UserRecord
) -> InterviewSessionRecord:
    """세션이 없거나 남의 세션이면 둘 다 404로 응답한다 — "이 ID의 세션이 존재하는데
    당신 것이 아니다"라는 정보(403)조차 노출하지 않기 위함(세션 ID는 순차 채번이
    아닌 UUID라 열거 공격 실효성은 낮지만, 다른 리소스와 일관된 정책을 유지한다)."""
    session = await interview_service.get_session(gateways, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다.")
    return session


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate, gateways: GatewaysDep, current_user: CurrentUserDep
) -> SessionRead:
    session = await interview_service.create_session(gateways, current_user.id, payload)
    return SessionRead.model_validate(session)


@router.get("/{session_id}", response_model=SessionRead)
async def get_session(
    session_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> SessionRead:
    session = await _get_own_session_or_404(gateways, session_id, current_user)
    return SessionRead.model_validate(session)


@router.post("/{session_id}/messages", response_model=TurnResponse)
async def send_message(
    session_id: uuid.UUID, payload: ChatMessageCreate, gateways: GatewaysDep, current_user: CurrentUserDep
) -> TurnResponse:
    session = await _get_own_session_or_404(gateways, session_id, current_user)
    user_turn, assistant_turn, updated_session = await interview_service.add_user_turn(
        gateways, session, payload.content
    )
    return TurnResponse(
        user_message=ChatMessageRead.model_validate(user_turn),
        assistant_message=ChatMessageRead.model_validate(assistant_turn),
        session=SessionRead.model_validate(updated_session),
    )


@router.post("/{session_id}/complete", response_model=SessionRead)
async def complete_session(
    session_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> SessionRead:
    session = await _get_own_session_or_404(gateways, session_id, current_user)
    session = await interview_service.complete_session(gateways, session)
    return SessionRead.model_validate(session)
