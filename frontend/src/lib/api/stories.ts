import { apiClient } from "@/lib/api/client";
import type { StoryCard } from "@/types/api";

export const storiesApi = {
  /** 완료된 세션 단위 카드(제목=질문, 부제=재조립 산문에서 재추출한 요약). '나의 이야기' 탭. */
  list: () => apiClient.get<StoryCard[]>("/api/v1/stories"),
};
