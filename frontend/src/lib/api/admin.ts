import { apiClient } from "@/lib/api/client";
import type { AdminSession } from "@/types/api";

export const adminApi = {
  /** 완료됐지만 Phase 2 후처리(산문 재조립)가 끝나지 않은 채 방치된 세션. */
  listStaleSessions: () => apiClient.get<AdminSession[]>("/api/v1/admin/stale-sessions"),
  /** 위기 대응 문구가 발화된 세션 — 사후 검토용. */
  listCrisisSessions: () => apiClient.get<AdminSession[]>("/api/v1/admin/crisis-sessions"),
};
