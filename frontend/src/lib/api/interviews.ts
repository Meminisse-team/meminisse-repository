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
  /** 미리보기로 보여준 다음 질문/사진을 거부('이 질문 넘어가기') — 아직 세션이
   * 없는 상태 전용. 건너뛴 뒤의 새 미리보기를 돌려준다. */
  skipNext: () => apiClient.post<NextItemPreview>("/api/v1/interview-sessions/skip-next"),
  get: (sessionId: string) =>
    apiClient.get<InterviewSessionDetail>(`/api/v1/interview-sessions/${sessionId}`),
  sendMessage: (sessionId: string, content: string) =>
    apiClient.post<TurnResponse>(`/api/v1/interview-sessions/${sessionId}/messages`, { content }),
  complete: (sessionId: string) =>
    apiClient.post<InterviewSession>(`/api/v1/interview-sessions/${sessionId}/complete`),
  /** 이미 열린 세션의 질문을 거부 — complete와 달리 산문 재조립/이벤트 추출 없이
   * SKIPPED로 종료되고, 같은 질문은 다시 배정되지 않는다. */
  skip: (sessionId: string) =>
    apiClient.post<InterviewSession>(`/api/v1/interview-sessions/${sessionId}/skip`),
};
