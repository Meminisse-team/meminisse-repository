import type { components } from "@/generated/api-types";


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

export type StoryCard = Required<components["schemas"]["StoryCardRead"]>;

export type StoryCardPage = Required<components["schemas"]["StoryCardPageRead"]>;
