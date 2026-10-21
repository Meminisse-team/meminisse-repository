import { apiClient } from "@/lib/api/client";
import type { StoryCard, StoryCardPage } from "@/types/api";

export const storiesApi = {
  /** 완료된 세션 단위 카드(제목=질문, 부제=재조립 산문에서 재추출한 요약). '나의 이야기' 탭.
   * limit/offset은 실제로 백엔드 DB 쿼리에서 페이지네이션된다(예전엔 프론트가 전체를
   * 받아 화면에 보일 7개만 잘라내는 방식이라, 세션이 많을수록 페이지를 넘겨도 요청
   * 자체는 항상 무거웠다 — 2026-07-17 수정). */
  list: ({ limit, offset }: { limit: number; offset: number }) =>
    apiClient.get<StoryCardPage>(`/api/v1/stories?limit=${limit}&offset=${offset}`),
  /** 재조립된 산문을 사용자가 직접 고쳐 저장한다. 저장 버튼을 눌렀을 때만 호출할 것 —
   * 타이핑마다 호출하면 안 된다(이벤트 재추출 LLM 호출이 그때마다 나간다). */
  updateProse: (sessionId: string, prose: string) =>
    apiClient.patch<StoryCard>(`/api/v1/stories/${sessionId}`, { prose }),
};
