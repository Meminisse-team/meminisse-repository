import { apiClient } from "@/lib/api/client";
import type { User } from "@/types/api";

export interface LoginInput {
  email: string;
  password: string;
}

export const authApi = {
  login: (input: LoginInput) => apiClient.post<{ authenticated: boolean }>("/api/v1/auth/login", input),
  logout: () => apiClient.post("/api/v1/auth/logout"),
  me: () => apiClient.get<User>("/api/v1/auth/me"),
};
