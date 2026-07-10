import { apiClient } from "@/lib/api/client";
import type { InterviewSession, SessionType, TurnResponse } from "@/types/api";

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
  get: (sessionId: string) =>
    apiClient.get<InterviewSession>(`/api/v1/interview-sessions/${sessionId}`),
  sendMessage: (sessionId: string, content: string) =>
    apiClient.post<TurnResponse>(`/api/v1/interview-sessions/${sessionId}/messages`, { content }),
  complete: (sessionId: string) =>
    apiClient.post<InterviewSession>(`/api/v1/interview-sessions/${sessionId}/complete`),
};
