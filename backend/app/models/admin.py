import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.models.base import Base


class AdminAuditLog(Base):
    """관리자가 사용자의 개인 서사 데이터(세션 대화·산문)를 열람할 때마다 남기는
    최소 감사 기록. 나중에 필요해져서 추가하면 그 이전 접근 이력을 소급할 수 없으므로
    관리자 대시보드 도입 시점부터 함께 넣는다."""
    __tablename__ = "admin_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action = Column(String(100), nullable=False)  # 예: "view_stale_sessions", "view_crisis_sessions"
    target_user_id = Column(UUID(as_uuid=True), nullable=True)
    target_session_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
