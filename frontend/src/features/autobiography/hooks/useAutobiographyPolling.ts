import { useEffect } from "react";
import { autobiographiesApi } from "../api/autobiographies";
import type { Autobiography, ChapterDraft, AutobiographyPollingStatus } from "@/types/api";

export function hasStatusChanged(bio: Autobiography, chapters: ChapterDraft[], status: AutobiographyPollingStatus) {
  const byId = new Map(chapters.map(chapter => [chapter.id, chapter]));
  return status.status !== bio.status || (status.final_content_ready && !bio.final_content) ||
    status.pdf_version !== bio.pdf_version || status.chapters.some(chapter => {
      const current = byId.get(chapter.id);
      return !current || chapter.updated_at !== current.updated_at || (chapter.has_content && current.content === null);
    });
}

/** Lightweight status first; fetch manuscript only on a delta. No overlapping polls. */
export function useAutobiographyPolling({ autobiography, chapters, enabled, onChanged, onError }: {
  autobiography: Autobiography | null; chapters: ChapterDraft[]; enabled: boolean;
  onChanged: () => Promise<unknown>; onError: (message: string) => void;
}) {
  useEffect(() => {
    if (!enabled || !autobiography) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const status = await autobiographiesApi.getPollingStatus(autobiography!.id);
        if (!cancelled && hasStatusChanged(autobiography!, chapters, status)) await onChanged();
      } catch {
        if (!cancelled) onError("자서전 정보를 불러오지 못했어요.");
      } finally {
        if (!cancelled) timer = setTimeout(tick, 4000);
      }
    }
    timer = setTimeout(tick, 4000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [autobiography, chapters, enabled, onChanged, onError]);
}
