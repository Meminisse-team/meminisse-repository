import { apiClient } from "@/lib/api/client";
import type {
  Autobiography,
  CustomizationConfirmRequest,
  CustomizationOptionsResponse,
  CustomizationRecommendationResponse,
  CustomizationSelectionRequest,
  SamplePreviewsResponse,
} from "@/types/api";

/** 자서전 말투/구성/컨셉 커스터마이징(backend/app/api/v1/autobiographies.py의
 * /{autobiography_id}/customization/* 6개 엔드포인트). consolidate(Phase 3) 완료 후,
 * toc/generate 이전에 거치는 선택적 단계 — 건너뛰고 바로 목차를 만들어도 동작한다. */
export const customizationApi = {
  getOptions: (autobiographyId: string) =>
    apiClient.get<CustomizationOptionsResponse>(
      `/api/v1/autobiographies/${autobiographyId}/customization/options`,
    ),
  /** Phase 3(consolidate) 완료 후엔 실제 이야기 내용을 근거로 한 추천("content_based"),
   * 그 전에는 답변한 질문들의 사전 태그를 집계한 즉석 힌트("tag_based")가 온다 — 언제
   * 호출해도 에러 없이 동작한다. */
  getRecommendations: (autobiographyId: string) =>
    apiClient.get<CustomizationRecommendationResponse>(
      `/api/v1/autobiographies/${autobiographyId}/customization/recommendations`,
    ),
  /** 카테고리별 1~2개. 옵션 목록에 없는 키거나 개수 범위를 벗어나면 400. */
  select: (autobiographyId: string, selection: CustomizationSelectionRequest) =>
    apiClient.post<Autobiography>(
      `/api/v1/autobiographies/${autobiographyId}/customization/select`,
      selection,
    ),
  /** 202 — Celery 큐잉만 하고 즉시 반환한다(최대 2×2×2=8회의 LLM 호출). select가 먼저
   * 성공해 있어야 한다(안 그러면 워커 안에서만 조용히 실패하고 previews가 계속 빈
   * 배열로 남는다). 완료 여부는 getPreviews를 폴링해 samples가 채워지는지로 확인. */
  generatePreviews: (autobiographyId: string) =>
    apiClient.post<{ detail: string }>(
      `/api/v1/autobiographies/${autobiographyId}/customization/previews`,
    ),
  getPreviews: (autobiographyId: string) =>
    apiClient.get<SamplePreviewsResponse>(
      `/api/v1/autobiographies/${autobiographyId}/customization/previews`,
    ),
  /** 8개 샘플 중 고른 SamplePreviewItem의 tone/structure/concept를 그대로 전달하면
   * 된다. 확정 이후 toc/generate가 이 조합을 자동으로 읽어 반영한다. */
  confirm: (autobiographyId: string, selection: CustomizationConfirmRequest) =>
    apiClient.post<Autobiography>(
      `/api/v1/autobiographies/${autobiographyId}/customization/confirm`,
      selection,
    ),
};
