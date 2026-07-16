import { apiClient } from "@/lib/api/client";
import type {
  AdminAuditLog,
  AdminDbRow,
  AdminDbTable,
  AdminLogLines,
  AdminLogService,
  AdminSession,
  AdminSessionDetail,
  AdminUserDetail,
} from "@/types/api";

export const adminApi = {
  /** 완료됐지만 Phase 2 후처리(산문 재조립)가 끝나지 않은 채 방치된 세션. */
  listStaleSessions: () => apiClient.get<AdminSession[]>("/api/v1/admin/stale-sessions"),
  /** 위기 대응 문구가 발화된 세션 — 사후 검토용. */
  listCrisisSessions: () => apiClient.get<AdminSession[]>("/api/v1/admin/crisis-sessions"),
  /** identifier는 유저 UUID 또는 이메일. */
  lookupUser: (identifier: string) =>
    apiClient.get<AdminUserDetail>(`/api/v1/admin/users/lookup?identifier=${encodeURIComponent(identifier)}`),
  updateUserSessionProse: (userId: string, sessionId: string, prose: string) =>
    apiClient.patch<AdminSessionDetail>(
      `/api/v1/admin/users/${userId}/sessions/${sessionId}/prose`,
      { prose },
    ),
  updateUserEmail: (userId: string, newEmail: string) =>
    apiClient.patch<AdminUserDetail>(`/api/v1/admin/users/${userId}/email`, { new_email: newEmail }),
  resetUserPassword: (userId: string, newPassword: string) =>
    apiClient.post<void>(`/api/v1/admin/users/${userId}/reset-password`, { new_password: newPassword }),
  listDbTable: (table: AdminDbTable, limit: number, offset: number) =>
    apiClient.get<AdminDbRow[]>(`/api/v1/admin/db/${table}?limit=${limit}&offset=${offset}`),
  listAuditLogs: (limit: number, offset: number) =>
    apiClient.get<AdminAuditLog[]>(`/api/v1/admin/audit-logs?limit=${limit}&offset=${offset}`),
  getAppLogs: (service: AdminLogService, lines: number) =>
    apiClient.get<AdminLogLines>(`/api/v1/admin/logs?service=${service}&lines=${lines}`),
};
