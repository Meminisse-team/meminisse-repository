/**
 * 백엔드(app/schemas/*.py, app/models/enums.py)와 1:1로 대응하는 타입 정의.
 * 백엔드 스키마가 바뀌면 이 파일도 함께 갱신해야 한다 — 자동 생성(OpenAPI codegen)은
 * 아직 도입하지 않았으므로 당분간 수동 동기화가 필요하다.
 */

export type UserStage = "onboarding" | "interview" | "publishing" | "published";
export type UserRole = "user" | "admin";
export type EducationLevel = "elementary" | "middle_school" | "high_school" | "university" | "graduate_school";
export type MaritalStatus = "single" | "married" | "divorced" | "widowed";
export type LifePeriod = "childhood" | "youth" | "adulthood" | "senior";
export type SessionType = "photo" | "fixed_question";
export type SessionStatus = "open" | "completed" | "skipped";
export type MessageRole = "user" | "assistant" | "system";
export type AssetType = "image" | "audio" | "video" | "document";
export type MediaAnalysisTrack = "text_document" | "pure_memory";
export type AutobiographyStatus = "in_progress" | "consolidated" | "published";
export type ConsentType = "data_collection" | "disclosure_realname" | "retention_extension";
export type ConsentGrantedBy = "self" | "guardian";
export type EventSourceType = "session_chat" | "document";
export type LifeMilestoneCategory =
  | "marriage"
  | "childbirth"
  | "career_change"
  | "illness"
  | "bereavement"
  | "relocation"
  | "retirement"
  | "other";

export interface User {
  id: string;
  email: string;
  name: string;
  birth_year: number | null;
  hometown: string | null;
  current_stage: UserStage;
  role: UserRole;
  education_level: EducationLevel | null;
  marital_status: MaritalStatus | null;
  has_children: boolean | null;
}

/** POST /api/v1/auth/oauth-sync 응답. is_new=true면 방금 프로필이 생성된
 * 첫 로그인이라는 뜻 — 프론트가 이 값으로 온보딩(프로필 완성) 진입 여부를
 * 결정한다(backend/app/schemas/user.py:OAuthSyncResponse). */
export interface OAuthSyncResponse {
  user: User;
  is_new: boolean;
}

export type OAuthProvider = "kakao" | "google";

/** POST /api/v1/auth/login, /api/v1/auth/refresh 응답. Supabase Auth가 발급한 세션을
 * 그대로 전달한다(backend/app/schemas/auth.py:TokenResponse). */
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface ConsentRecord {
  id: string;
  user_id: string;
  consent_type: ConsentType;
  notice_version: string;
  granted_by: ConsentGrantedBy;
  granted_at: string;
  revoked_at: string | null;
  character_id: string | null;
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

/** GET /api/v1/interview-sessions/next-preview 응답 — 세션을 만들지 않고 다음
 * 질문/사진 대화의 인사말만 미리 보여준다(backend/app/schemas/interview.py:
 * NextItemPreviewRead). session_type이 null이면 배정할 항목이 없다는 뜻(질문·사진
 * 큐를 모두 마침) — 그래도 opening_message는 항상 채워져 있다. */
export interface NextItemPreview {
  session_type: SessionType | null;
  linked_media_asset_id: string | null;
  opening_message: string;
}

/** GET /api/v1/interview-sessions/{id} 전용 — 목록 조회(InterviewSession)에는 없는
 * chat_logs가 포함된다(backend/app/schemas/interview.py:SessionDetailRead). */
export interface InterviewSessionDetail extends InterviewSession {
  chat_logs: ChatMessage[];
}

/** GET /api/v1/admin/stale-sessions, /api/v1/admin/crisis-sessions 응답 단위 —
 * SessionRead와 형태가 같다(backend/app/schemas/admin.py:AdminSessionRead). */
export type AdminSession = InterviewSession;

/** GET /api/v1/events 응답 단위 — '나의 이야기' 탭(backend/app/schemas/event.py:EventRead). */
export interface EventItem {
  id: string;
  source_type: EventSourceType;
  session_id: string | null;
  media_asset_id: string | null;
  life_period: LifePeriod | null;
  occurred_at_label: string | null;
  place: string | null;
  people: string | null;
  one_line_summary: string;
  prose_paragraph: string;
  emotion_tag: string | null;
  emotion_intensity: number | null;
  emotion_inferred: boolean;
  is_must_include: boolean;
  life_milestone_category: LifeMilestoneCategory | null;
  created_at: string;
}

/** GET /api/v1/stories 응답 단위 — 사건이 아니라 완료된 세션 단위 카드
 * (backend/app/schemas/story.py:StoryCardRead). 제목은 그 세션이 다룬 질문/사진
 * 오프닝 문구 그 자체, 부제는 재조립된 산문(prose)으로부터 재추출한 요약 라벨. */
export interface StoryCard {
  session_id: string;
  title: string;
  subtitle: string | null;
  prose: string;
  completed_at: string | null;
  /** true면 세션은 끝났지만 산문 재조립(Celery)이 아직 안 끝난 placeholder 카드
   * — prose는 빈 문자열이다. "생성 중..." 임시 셀로 표시한다. */
  is_generating: boolean;
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
  /** Azure Vision 캡션 — 사진의 시각적 내용을 설명하는 한 문장(예: "집 앞에서 5명이
   * 함께 찍은 사진"). PHOTO 세션 오프닝 질문의 재료가 된다. 분석 전이거나 Azure
   * 미설정이면 null. */
  image_caption: string | null;
  /** Azure Vision이 사진 속에서 읽어낸 인쇄/손글씨 텍스트. analysis_track이
   * "text_document"일 때만 채워진다. */
  image_ocr_text: string | null;
  user_comment: string | null;
  created_at: string;
}

/** toc/generate가 만드는 후보 하나(backend/app/agents/prompts.py의 Structured Outputs
 * 스키마와 1:1). candidate는 "index" 필드 없이 배열 순번으로만 식별된다 — toc/select에
 * 보내는 candidate_index가 바로 이 배열의 순번(0/1/2)이다. */
export interface TocChapterCandidate {
  chapter_index: number;
  title: string;
  theme_keywords: string[];
}

export interface TocCandidate {
  chapters: TocChapterCandidate[];
}

export interface TocData {
  generated_at: string;
  candidates: TocCandidate[];
  selected_candidate_index: number | null;
}

export interface Autobiography {
  id: string;
  user_id: string;
  title: string | null;
  status: AutobiographyStatus;
  toc_data: TocData | null;
  style_bible: Record<string, unknown> | null;
  book_synopsis: string | null;
  /** finalize 완료 후에만 채워지는 전체 원고(챕터 구분 없는 단일 텍스트). */
  final_content: string | null;
  /** pdf/generate 완료 후에만 채워지는 조판된 국판(A5) PDF의 S3 URL. */
  pdf_url: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /customization/options 응답 항목 하나 — 말투/구성/컨셉 선택지 공통 형태
 * (backend/app/schemas/autobiography.py:CustomizationOptionItem). CONCEPT_OPTIONS의
 * 일부 항목은 example이 없어(backend/app/agents/prompts.py) null일 수 있다. */
export interface CustomizationOptionItem {
  key: string;
  name: string;
  description: string;
  example: string | null;
}

/** GET /api/v1/autobiographies/{id}/customization/options 응답
 * (backend/app/schemas/autobiography.py:CustomizationOptionsResponse). */
export interface CustomizationOptionsResponse {
  tones: CustomizationOptionItem[];
  structures: CustomizationOptionItem[];
  concepts: CustomizationOptionItem[];
}

export type CustomizationRecommendationSource = "content_based" | "tag_based";

/** GET /api/v1/autobiographies/{id}/customization/recommendations 응답
 * (backend/app/schemas/autobiography.py:CustomizationRecommendationResponse).
 * tones/structures/concepts는 각각 최대 2개, 빈 배열일 수 있다. source가
 * "content_based"면 Phase 3(consolidate) 완료 후 실제 이야기 내용을 근거로 한
 * 추천이라 reasoning이 채워지고, "tag_based"면 답변한 질문들의 사전 태그를 집계한
 * 즉석 힌트라 reasoning은 항상 null이다. */
export interface CustomizationRecommendationResponse {
  tones: string[];
  structures: string[];
  concepts: string[];
  source: CustomizationRecommendationSource;
  reasoning: string | null;
}

/** POST .../customization/select 요청 바디(backend/app/schemas/autobiography.py:
 * CustomizationSelectionRequest). 카테고리별 1~2개. */
export interface CustomizationSelectionRequest {
  tones: string[];
  structures: string[];
  concepts: string[];
}

/** GET .../customization/previews, style_bible.customization.previews 응답 단위
 * (backend/app/schemas/autobiography.py:SamplePreviewItem). tone/structure/concept는
 * POST .../customization/confirm에 그대로 되돌려 보내는 키다. */
export interface SamplePreviewItem {
  tone: string;
  structure: string;
  concept: string;
  tone_name: string;
  structure_name: string;
  concept_name: string;
  preview_text: string;
}

export interface SamplePreviewsResponse {
  samples: SamplePreviewItem[];
}

/** POST .../customization/confirm 요청 바디(backend/app/schemas/autobiography.py:
 * CustomizationConfirmRequest) — 8개 샘플 중 고른 SamplePreviewItem의
 * tone/structure/concept 필드를 그대로 전달하면 된다. */
export interface CustomizationConfirmRequest {
  tone: string;
  structure: string;
  concept: string;
}

/** Autobiography.style_bible.customization의 shape(백엔드 style_bible 자체는 dict라
 * Autobiography.style_bible: Record<string, unknown>으로만 느슨히 타입화돼 있음 — 이
 * 부분만 읽을 때 이 타입으로 좁혀 쓴다. backend/app/services/autobiography_service.py:
 * save_customization_selection/generate_sample_previews/confirm_customization 참조). */
export interface CustomizationState {
  selected_at: string;
  tones: string[];
  structures: string[];
  concepts: string[];
  confirmed: (CustomizationConfirmRequest & { confirmed_at: string }) | null;
  previews: SamplePreviewItem[] | null;
}

export type DraftStatus = "draft" | "reviewed" | "finalized";

/** GET /api/v1/autobiographies/{id}/chapters 응답 단위
 * (backend/app/schemas/autobiography.py:ChapterDraftRead). */
export interface ChapterDraft {
  id: string;
  autobiography_id: string;
  chapter_index: number;
  title: string | null;
  chapter_synopsis: string | null;
  content: string | null;
  source_event_ids: string[];
  factcheck_report: Record<string, unknown> | null;
  groundedness_report: Record<string, unknown> | null;
  status: DraftStatus;
  created_at: string;
  updated_at: string;
}
