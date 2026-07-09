import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.schemas.interview import ChatMessageCreate, ChatMessageRead, SessionCreate, SessionRead, TurnResponse
from app.services import interview_service

router = APIRouter(prefix="/interview-sessions", tags=["interviews"])


async def _get_session_or_404(db: DbSession, session_id: uuid.UUID):
    session = await interview_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다.")
    return session


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, db: DbSession) -> SessionRead:
    session = await interview_service.create_session(db, payload)
    return SessionRead.model_validate(session)


@router.get("/{session_id}", response_model=SessionRead)
async def get_session(session_id: uuid.UUID, db: DbSession) -> SessionRead:
    session = await _get_session_or_404(db, session_id)
    return SessionRead.model_validate(session)


@router.post("/{session_id}/messages", response_model=TurnResponse)
async def send_message(session_id: uuid.UUID, payload: ChatMessageCreate, db: DbSession) -> TurnResponse:
    session = await _get_session_or_404(db, session_id)
    user_turn, assistant_turn = await interview_service.add_user_turn(db, session, payload.content)
    return TurnResponse(
        user_message=ChatMessageRead.model_validate(user_turn),
        assistant_message=ChatMessageRead.model_validate(assistant_turn),
        session=SessionRead.model_validate(session),
    )


@router.post("/{session_id}/complete", response_model=SessionRead)
async def complete_session(session_id: uuid.UUID, db: DbSession) -> SessionRead:
    session = await _get_session_or_404(db, session_id)
    session = await interview_service.complete_session(db, session)
    return SessionRead.model_validate(session)
