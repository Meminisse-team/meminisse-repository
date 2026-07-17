import { apiClient } from "@/lib/api/client";
import type { Autobiography, ChapterDraft, PhotoPlacement } from "@/types/api";

export const autobiographiesApi = {
  /** get-or-create — 미완성(final_content 없음) 버전이 있으면 이어서, 없으면(전부
   * 완성됐거나 하나도 없으면) 새로 자동 생성된다. */
  get: (userId: string) => apiClient.get<Autobiography>(`/api/v1/autobiographies/${userId}`),
  /** "나의 책장" 전용 — 이 유저가 완성한 자서전 전체(최신순). */
  listFinished: (userId: string) =>
    apiClient.get<Autobiography[]>(`/api/v1/autobiographies/${userId}/finished`),
  /** 202 — Celery 큐잉만 하고 즉시 반환한다(중복 이벤트 병합 + 중요도 산정 + 스타일
   * 바이블 생성). 완료되면 get()의 status가 "consolidated"로 바뀐다. */
  consolidate: (userId: string) =>
    apiClient.post<{ detail: string }>(`/api/v1/autobiographies/${userId}/consolidate`),
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
  /** PDF 조판 직전, 수록할 사진과 배치(고정 슬롯)를 통째로 교체 저장한다(PUT
   * 시맨틱 — 보낸 배열이 전체 상태, 빈 배열 = 수록 사진 없음으로 확정). */
  updatePhotoPlacements: (autobiographyId: string, placements: PhotoPlacement[]) =>
    apiClient.put<Autobiography>(`/api/v1/autobiographies/${autobiographyId}/photo-placements`, {
      placements,
    }),
  /** 202 — 국판(A5) PDF 조판을 큐잉한다. final_content가 없으면 워커에서 실패한다.
   * 완료되면 get()의 pdf_url이 채워진다. */
  generatePdf: (autobiographyId: string) =>
    apiClient.post<{ detail: string }>(`/api/v1/autobiographies/${autobiographyId}/pdf/generate`),
};
