import { apiClient } from "@/lib/api/client";

export interface Disclosures {
  non_medical_service: string;
}

/** 인증 불필요 — 온보딩 동의 화면(가입 전)에서부터 노출돼야 하는 3층 고지(비의료
 * 서비스 안내) 문구를 가져온다. */
export const legalApi = {
  getDisclosures: () => apiClient.get<Disclosures>("/api/v1/legal/disclosures"),
};
