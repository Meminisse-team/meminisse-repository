import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base, str_enum
from app.models.enums import ConsentGrantedBy, ConsentType


class ConsentRecord(Base):
    """
    동의 기록(기획안 5절 동의 주체 분리, 6절 주의의무 이행 증빙).

    자녀가 온보딩을 대신 세팅하더라도 데이터 수집·이용 동의는 정보주체(부모) 본인에게 첫
    세션에서 직접 획득해야 하므로 granted_by로 행위자를 구분한다. 고지 문구 버전과 동의
    시각을 남겨 분쟁 발생 시 서비스가 고지 의무를 이행했음을 입증할 수 있게 한다.
    """
    __tablename__ = "consent_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consent_type = Column(str_enum(ConsentType, name="consenttype"), nullable=False)
    notice_version = Column(String(50), nullable=False)
    granted_by = Column(str_enum(ConsentGrantedBy, name="consentgrantedby"), nullable=False)
    granted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # 사용자가 이후 동의를 철회한 경우(예: 실명 유지 취소). null이면 유효 상태.
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="consent_records")
