import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import GatewaysDep
from app.gateways.dto import InterviewSessionRecord
from app.schemas.interview import ChatMessageCreate, ChatMessageRead, SessionCreate, SessionRead, TurnResponse
from app.services import interview_service

router = APIRouter(prefix="/interview-sessions", tags=["interviews"])


async def _get_session_or_404(gateways: GatewaysDep, session_id: uuid.UUID) -> InterviewSessionRecord:
    session = await interview_service.get_session(gateways, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다.")
    return session


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, gateways: GatewaysDep) -> SessionRead:
    session = await interview_service.create_session(gateways, payload)
    return SessionRead.model_validate(session)


@router.get("/{session_id}", response_model=SessionRead)
async def get_session(session_id: uuid.UUID, gateways: GatewaysDep) -> SessionRead:
    session = await _get_session_or_404(gateways, session_id)
    return SessionRead.model_validate(session)


@router.post("/{session_id}/messages", response_model=TurnResponse)
async def send_message(
    session_id: uuid.UUID, payload: ChatMessageCreate, gateways: GatewaysDep
) -> TurnResponse:
    session = await _get_session_or_404(gateways, session_id)
    user_turn, assistant_turn, updated_session = await interview_service.add_user_turn(
        gateways, session, payload.content
    )
    return TurnResponse(
        user_message=ChatMessageRead.model_validate(user_turn),
        assistant_message=ChatMessageRead.model_validate(assistant_turn),
        session=SessionRead.model_validate(updated_session),
    )


@router.post("/{session_id}/complete", response_model=SessionRead)
async def complete_session(session_id: uuid.UUID, gateways: GatewaysDep) -> SessionRead:
    session = await _get_session_or_404(gateways, session_id)
    session = await interview_service.complete_session(gateways, session)
    return SessionRead.model_validate(session)
