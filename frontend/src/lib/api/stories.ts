import { apiClient } from "@/lib/api/client";
import type { StoryCard } from "@/types/api";

export const storiesApi = {
  /** 완료된 세션 단위 카드(제목=질문, 부제=재조립 산문에서 재추출한 요약). '나의 이야기' 탭. */
  list: () => apiClient.get<StoryCard[]>("/api/v1/stories"),
  /** 재조립된 산문을 사용자가 직접 고쳐 저장한다. 저장 버튼을 눌렀을 때만 호출할 것 —
   * 타이핑마다 호출하면 안 된다(이벤트 재추출 LLM 호출이 그때마다 나간다). */
  updateProse: (sessionId: string, prose: string) =>
    apiClient.patch<StoryCard>(`/api/v1/stories/${sessionId}`, { prose }),
};
