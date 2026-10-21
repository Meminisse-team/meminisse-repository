import type { components } from "@/generated/api-types";


export type LifePeriod = Required<components["schemas"]["LifePeriod"]>;

export type SessionType = Required<components["schemas"]["SessionType"]>;

export type SessionStatus = Required<components["schemas"]["SessionStatus"]>;

export type MessageRole = Required<components["schemas"]["MessageRole"]>;

export type InterviewSession = Required<components["schemas"]["SessionRead"]>;

export type ChatMessage = Required<components["schemas"]["ChatMessageRead"]>;

export type TurnResponse = Required<components["schemas"]["TurnResponse"]>;

export type NextItemPreview = Required<components["schemas"]["NextItemPreviewRead"]>;

export type InterviewSessionDetail = Required<components["schemas"]["SessionDetailRead"]>;
