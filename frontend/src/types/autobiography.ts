import type { components } from "@/generated/api-types";


export type AutobiographyStatus = Required<components["schemas"]["AutobiographyStatus"]>;

export interface TocChapterCandidate {
  chapter_index: number;
  title: string;
  theme_keywords: string[];
  /** 이 챕터가 속한 Part 번호. 구버전 toc_data(Part 구조 도입 이전)는 없을 수 있다. */
  part_index?: number;
}

export interface TocPart {
  part_index: number;
  part_title: string;
  part_arc: string;
  /** select_toc_candidate 이후에만 채워지는, book_synopsis를 반영한 풍부한 Part 시놉시스. */
  part_synopsis?: string;
}

export interface TocCandidate {
  chapters: TocChapterCandidate[];
  /** 2개 이상일 때만 Part 구조로 취급한다 — 없거나 1개면(예: 에피소드 중심 구성) 평평한 목록. */
  parts?: TocPart[];
}

export interface TocData {
  generated_at: string;
  candidates: TocCandidate[];
  selected_candidate_index: number | null;
}

export type PhotoPlacementSlot = "chapter_top" | "full_page_before";

export interface PhotoPlacement {
  media_asset_id: string;
  chapter_index: number;
  slot: PhotoPlacementSlot;
  caption: string | null;
}

export type Autobiography = Omit<Required<components["schemas"]["AutobiographyRead"]>, "toc_data" | "progress" | "photo_placements"> & { toc_data: TocData | null; progress: Required<components["schemas"]["AutobiographyProgress"]>; photo_placements: PhotoPlacement[] | null; };

export type ChapterStatusItem = Required<components["schemas"]["ChapterStatusItem"]>;

export type AutobiographyPollingStatus = Required<components["schemas"]["AutobiographyPollingStatus"]>;

export type CustomizationOptionItem = components["schemas"]["CustomizationOptionItem"];

export type CustomizationOptionsResponse = Required<components["schemas"]["CustomizationOptionsResponse"]>;

export type CustomizationRecommendationSource = "content_based" | "tag_based";

export type CustomizationRecommendationResponse = Required<components["schemas"]["CustomizationRecommendationResponse"]>;

export type CustomizationSelectionRequest = Required<components["schemas"]["CustomizationSelectionRequest"]>;

export type SamplePreviewItem = components["schemas"]["SamplePreviewItem"];

export type SamplePreviewsResponse = Required<components["schemas"]["SamplePreviewsResponse"]>;

export type CustomizationConfirmRequest = Required<components["schemas"]["CustomizationConfirmRequest"]>;

export interface CustomizationState {
  selected_at: string;
  tones: string[];
  structures: string[];
  concepts: string[];
  confirmed: (CustomizationConfirmRequest & { confirmed_at: string }) | null;
  previews: SamplePreviewItem[] | null;
}

export type DraftStatus = Required<components["schemas"]["DraftStatus"]>;

export interface FactcheckReport {
  checked_at: string;
  total_facts: number;
  unchecked_facts: number;
  flags: { fact_type: string; raw_text: string; reason: string }[];
}

export interface GroundednessReport {
  checked: boolean;
  flags: { sentence: string; entailment_score?: number; reason: string }[];
  total_sentences?: number;
  source_event_count: number;
  note?: string;
}

export type ChapterDraft = Omit<components["schemas"]["ChapterDraftRead"], "factcheck_report" | "groundedness_report"> & { factcheck_report: FactcheckReport | null; groundedness_report: GroundednessReport | null; };
