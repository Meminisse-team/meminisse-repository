
import type { ChapterDraft } from "@/types/api";

export function joinChaptersPreview(chapters: ChapterDraft[]): string {
  return chapters
    .map((chapter) => `[${chapter.chapter_index}장. ${chapter.title ?? ""}]\n${chapter.content ?? ""}`)
    .join("\n\n");
}
