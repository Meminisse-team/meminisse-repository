import { apiClient } from "@/lib/api/client";
import type { Autobiography, ChapterDraft } from "@/types/api";

export const autobiographiesApi = {
  /** get-or-create — 처음 호출하는 순간 in_progress 상태로 자동 생성된다. */
  get: (userId: string) => apiClient.get<Autobiography>(`/api/v1/autobiographies/${userId}`),
  /** 목차 후보 3개를 동기적으로 생성한다. Phase 3(이벤트 병합)가 아직이면 409. */
  generateToc: (autobiographyId: string) =>
    apiClient.post<Autobiography>(`/api/v1/autobiographies/${autobiographyId}/toc/generate`),
  /** candidateIndex는 toc_data.candidates 배열의 순번(0/1/2) — 재호출 시 챕터 초안이
   * 전부 교체되므로 재선택도 안전하다. */
  selectToc: (autobiographyId: string, candidateIndex: number) =>
    apiClient.post<Autobiography>(`/api/v1/autobiographies/${autobiographyId}/toc/select`, {
      candidate_index: candidateIndex,
    }),
  listChapters: (autobiographyId: string) =>
    apiClient.get<ChapterDraft[]>(`/api/v1/autobiographies/${autobiographyId}/chapters`),
  /** 202 — Celery 큐잉만 하고 즉시 반환한다. 완료 여부는 listChapters를 폴링해 확인. */
  writeChapter: (autobiographyId: string, chapterDraftId: string) =>
    apiClient.post<{ detail: string }>(
      `/api/v1/autobiographies/${autobiographyId}/chapters/${chapterDraftId}/write`,
    ),
  /** 202 — 마찬가지로 비동기. 완료되면 get()의 final_content가 채워진다. */
  finalize: (autobiographyId: string) =>
    apiClient.post<{ detail: string }>(`/api/v1/autobiographies/${autobiographyId}/finalize`),
};
