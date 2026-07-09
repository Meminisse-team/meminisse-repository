/**
 * 백엔드(app/schemas/*.py, app/models/enums.py)와 1:1로 대응하는 타입 정의.
 * 백엔드 스키마가 바뀌면 이 파일도 함께 갱신해야 한다 — 자동 생성(OpenAPI codegen)은
 * 아직 도입하지 않았으므로 당분간 수동 동기화가 필요하다.
 */

export type UserStage = "onboarding" | "interview" | "publishing" | "published";
export type LifePeriod = "childhood" | "youth" | "adulthood" | "senior";
export type SessionType = "photo" | "fixed_question";
export type SessionStatus = "open" | "completed" | "skipped";
export type MessageRole = "user" | "assistant" | "system";
export type AssetType = "image" | "audio" | "video" | "document";
export type MediaAnalysisTrack = "text_document" | "pure_memory";
export type AutobiographyStatus = "in_progress" | "consolidated" | "published";

export interface User {
  id: string;
  email: string;
  name: string;
  birth_year: number | null;
  hometown: string | null;
  current_stage: UserStage;
}

export interface InterviewSession {
  id: string;
  user_id: string;
  session_type: SessionType;
  question_id: string | null;
  linked_media_asset_id: string | null;
  status: SessionStatus;
  slots_filled: Record<string, boolean>;
  followup_count: number;
  is_must_include: boolean;
  started_at: string;
  completed_at: string | null;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  turn_index: number;
  created_at: string;
}

export interface TurnResponse {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  session: InterviewSession;
}

export interface MediaAsset {
  id: string;
  user_id: string;
  session_id: string | null;
  s3_url: string;
  asset_type: AssetType;
  age_at_time: number | null;
  location_at_time: string | null;
  people_at_time: string | null;
  life_period_mapped: LifePeriod | null;
  analysis_track: MediaAnalysisTrack | null;
  user_comment: string | null;
  created_at: string;
}

export interface Autobiography {
  id: string;
  user_id: string;
  title: string | null;
  status: AutobiographyStatus;
  toc_data: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}
