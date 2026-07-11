import { apiClient } from "@/lib/api/client";
import type { EventItem } from "@/types/api";

export const eventsApi = {
  /** 본인의 검증된(verified=true) 사건을 최근 대화순으로. '나의 이야기' 탭. */
  list: () => apiClient.get<EventItem[]>("/api/v1/events"),
};
