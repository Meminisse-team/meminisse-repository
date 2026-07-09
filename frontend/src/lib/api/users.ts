import { apiClient } from "@/lib/api/client";
import type { User } from "@/types/api";

export interface CreateUserInput {
  email: string;
  name: string;
  birth_year?: number;
  hometown?: string;
}

export const usersApi = {
  create: (input: CreateUserInput) => apiClient.post<User>("/api/v1/users", input),
  get: (userId: string) => apiClient.get<User>(`/api/v1/users/${userId}`),
};
