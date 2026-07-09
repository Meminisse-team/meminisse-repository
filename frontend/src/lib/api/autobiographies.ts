import { apiClient } from "@/lib/api/client";
import type { Autobiography } from "@/types/api";

export const autobiographiesApi = {
  get: (userId: string) => apiClient.get<Autobiography>(`/api/v1/autobiographies/${userId}`),
};
