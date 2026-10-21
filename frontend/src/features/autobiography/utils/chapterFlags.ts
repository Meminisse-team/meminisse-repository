"use client";

import type { ChapterDraft } from "@/types/api";

export function chapterFlagCount(chapter: ChapterDraft): number {
  return (
    (chapter.factcheck_report?.flags.length ?? 0) + (chapter.groundedness_report?.flags.length ?? 0)
  );
}

export const STATUS_LABEL: Record<ChapterDraft["status"], string> = {
  draft: "집필 전",
  reviewed: "집필 완료",
  finalized: "최종 확정",
};
