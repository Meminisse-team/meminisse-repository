"""관리자 대시보드 응답 스키마. app/services/admin_service.py 참조."""

from app.schemas.interview import SessionRead

# 세션 요약 형태가 SessionRead와 동일해 별칭으로만 노출한다 — 관리자 뷰라고
# 필드가 달라질 이유가 없다(둘 다 개인정보인 chat_logs/session_prose는 제외).
AdminSessionRead = SessionRead
