import { apiClient } from "@/lib/api/client";
import type { ConsentGrantedBy, ConsentRecord, ConsentType, User } from "@/types/api";

export interface CreateUserInput {
  email: string;
  name: string;
  /** 평문 비밀번호. Supabase Auth로 그대로 전달되며 이 서버는 저장하지 않는다
   * (backend/app/schemas/user.py:UserCreate 참조). */
  password: string;
  birth_year?: number;
  hometown?: string;
}

export interface CreateConsentInput {
  consent_type: ConsentType;
  notice_version: string;
  granted_by: ConsentGrantedBy;
}

export interface UpdateProfileInput {
  name?: string;
  birth_year?: number;
  hometown?: string;
}

export const usersApi = {
  create: (input: CreateUserInput) => apiClient.post<User>("/api/v1/users", input),
  get: (userId: string) => apiClient.get<User>(`/api/v1/users/${userId}`),
  /** 소셜 로그인 온보딩(프로필 완성 단계)이 주 용도 — 계정 생성 시점에 없던
   * 생년/고향을 로그인 이후 채운다(backend/app/api/v1/users.py:PATCH). */
  updateProfile: (userId: string, input: UpdateProfileInput) =>
    apiClient.patch<User>(`/api/v1/users/${userId}`, input),
  createConsent: (userId: string, input: CreateConsentInput) =>
    apiClient.post<ConsentRecord>(`/api/v1/users/${userId}/consents`, input),
  listConsents: (userId: string) => apiClient.get<ConsentRecord[]>(`/api/v1/users/${userId}/consents`),
};
