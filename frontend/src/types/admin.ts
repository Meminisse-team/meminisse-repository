import type { components } from "@/generated/api-types";


export type AdminSession = InterviewSession;

export type AdminSessionDetail = Required<components["schemas"]["AdminSessionDetailRead"]>;

export type AdminUserDetail = Required<components["schemas"]["AdminUserDetail"]>;

export type AdminDbTable = Required<components["schemas"]["AdminDbTable"]>;

export type AdminDbRow = Record<string, unknown>;

export type AdminAuditLog = Required<components["schemas"]["AdminAuditLogRead"]>;

export type AdminLogService = Required<components["schemas"]["AdminLogService"]>;

export type AdminLogLines = Required<components["schemas"]["AdminLogLinesRead"]>;

import type { InterviewSession } from "./interview";
