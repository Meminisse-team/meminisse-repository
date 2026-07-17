import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, GatewaysDep
from app.gateways.dto import InterviewSessionRecord, UserRecord
from app.schemas.interview import (
    ChatMessageCreate,
    ChatMessageRead,
    NextItemPreviewRead,
    SessionCreate,
    SessionDetailRead,
    SessionRead,
    TurnResponse,
)
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
    try:
        session = await interview_service.create_session(gateways, current_user.id, payload)
    except interview_service.NoRemainingQuestionsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "배정할 다음 고정 질문이 없습니다. 이미 모든 질문에 답변하셨습니다.",
        )
    return SessionRead.model_validate(session)


@router.get("", response_model=list[SessionRead])
async def list_sessions(gateways: GatewaysDep, current_user: CurrentUserDep) -> list[SessionRead]:
    """본인 세션 전체를 최신순으로 반환한다(대시보드 '오늘의 대화'가 이어갈 세션을
    찾거나 미리보기를 보여줄 때 사용). chat_logs는 포함하지 않는다 —
    GET /{session_id}로 개별 조회할 것."""
    sessions = await interview_service.list_sessions(gateways, current_user.id)
    return [SessionRead.model_validate(session) for session in sessions]


@router.get("/next-preview", response_model=NextItemPreviewRead)
async def preview_next(gateways: GatewaysDep, current_user: CurrentUserDep) -> NextItemPreviewRead:
    """새 대화창을 열 때, 세션을 만들기 전에 다음 질문/사진이 무엇일지 미리 보여준다
    (세션 자체는 여전히 첫 발화 시점에 생성 — 빈 세션 방지, ChatOverlay.tsx 참조).
    반드시 `/{session_id}` 라우트보다 먼저 등록해야 한다 — 안 그러면 "next-preview"가
    session_id 경로 파라미터로 잘못 매칭된다."""
    preview = await interview_service.preview_next_item(gateways, current_user.id)
    return NextItemPreviewRead.model_validate(preview)


@router.post("/skip-next", response_model=NextItemPreviewRead)
async def skip_next(gateways: GatewaysDep, current_user: CurrentUserDep) -> NextItemPreviewRead:
    """미리보기로 보여준 다음 질문/사진을 사용자가 거부('이 질문 넘어가기')한 경우 —
    세션이 아직 없으므로 그 항목을 SKIPPED 세션으로 배정 처리하고, 건너뛴 뒤의
    새 미리보기를 반환한다(프론트가 다음 질문을 한 번의 왕복으로 이어 보여줄 수
    있게). 반드시 `/{session_id}` 라우트보다 먼저 등록한다."""
    try:
        preview = await interview_service.skip_next_item(gateways, current_user.id)
    except interview_service.NoRemainingQuestionsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "건너뛸 다음 질문이 없습니다. 이미 모든 질문에 답변하셨습니다.",
        )
    return NextItemPreviewRead.model_validate(preview)


@router.get("/{session_id}", response_model=SessionDetailRead)
async def get_session(
    session_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> SessionDetailRead:
    session = await _get_own_session_or_404(gateways, session_id, current_user)
    return SessionDetailRead.model_validate(session)


@router.post("/{session_id}/messages", response_model=TurnResponse)
async def send_message(
    session_id: uuid.UUID, payload: ChatMessageCreate, gateways: GatewaysDep, current_user: CurrentUserDep
) -> TurnResponse:
    session = await _get_own_session_or_404(gateways, session_id, current_user)
    try:
        user_turn, assistant_turn, updated_session = await interview_service.add_user_turn(
            gateways, session, payload.content
        )
    except interview_service.SessionNotOpenError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미 종료된 대화입니다. 새 대화를 시작해주세요.",
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


@router.post("/{session_id}/skip", response_model=SessionRead)
async def skip_session(
    session_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> SessionRead:
    """열려 있는 세션의 질문을 사용자가 거부한 경우 — complete와 달리 Phase 2
    후처리 없이 SKIPPED로 전이한다(interview_service.skip_session 참조)."""
    session = await _get_own_session_or_404(gateways, session_id, current_user)
    try:
        session = await interview_service.skip_session(gateways, session)
    except interview_service.SessionNotOpenError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미 종료된 대화입니다. 새 대화를 시작해주세요.",
        )
    return SessionRead.model_validate(session)
