import { apiClient } from "@/lib/api/client";
import type { InterviewSession, SessionType, TurnResponse } from "@/types/api";

export interface CreateSessionInput {
  user_id: string;
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
