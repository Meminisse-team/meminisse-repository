import { apiClient } from "@/lib/api/client";
import type { TokenResponse, User } from "@/types/api";

export interface LoginInput {
  email: string;
  password: string;
}

export const authApi = {
  login: (input: LoginInput) => apiClient.post<TokenResponse>("/api/v1/auth/login", input),
  refresh: (refreshToken: string) =>
    apiClient.post<TokenResponse>("/api/v1/auth/refresh", { refresh_token: refreshToken }),
  me: () => apiClient.get<User>("/api/v1/auth/me"),
};
