import { apiClient } from "@/lib/api/client";
import type {
  InterviewSession,
  InterviewSessionDetail,
  NextItemPreview,
  SessionType,
  TurnResponse,
} from "@/types/api";

/** user_id는 없다 — 인증 토큰의 로그인 사용자로 서버가 항상 고정한다
 * (backend/app/schemas/interview.py:SessionCreate 참조). */
export interface CreateSessionInput {
  session_type: SessionType;
  question_id?: string;
  linked_media_asset_id?: string;
}

export const interviewsApi = {
  create: (input: CreateSessionInput) =>
    apiClient.post<InterviewSession>("/api/v1/interview-sessions", input),
  /** 본인 세션 전체를 최신순으로. chat_logs는 포함하지 않는다(get으로 개별 조회). */
  list: () => apiClient.get<InterviewSession[]>("/api/v1/interview-sessions"),
  /** 세션을 만들지 않고, 새 대화를 시작하면 뭘 묻게 될지만 미리 본다(빈 세션
   * 방지 — 세션 자체는 여전히 첫 발화 시점에 생성된다, ChatOverlay.tsx 참조). */
  previewNext: () => apiClient.get<NextItemPreview>("/api/v1/interview-sessions/next-preview"),
  get: (sessionId: string) =>
    apiClient.get<InterviewSessionDetail>(`/api/v1/interview-sessions/${sessionId}`),
  sendMessage: (sessionId: string, content: string) =>
    apiClient.post<TurnResponse>(`/api/v1/interview-sessions/${sessionId}/messages`, { content }),
  complete: (sessionId: string) =>
    apiClient.post<InterviewSession>(`/api/v1/interview-sessions/${sessionId}/complete`),
};
