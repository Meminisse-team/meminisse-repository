import { apiClient } from "@/lib/api/client";
import type {
  ConsentGrantedBy,
  ConsentRecord,
  ConsentType,
  EducationLevel,
  MaritalStatus,
  User,
} from "@/types/api";

export interface CreateUserInput {
  email: string;
  name: string;
  /** 평문 비밀번호. Supabase Auth로 그대로 전달되며 이 서버는 저장하지 않는다
   * (backend/app/schemas/user.py:UserCreate 참조). */
  password: string;
  birth_year?: number;
  hometown?: string;
  /** 온보딩 라디오 버튼 선택 응답. 안 보내면 "응답하지 않음"과 동일하게 취급되어
   * 동적 질문 필터링이 그 정보를 전제로 한 질문도 그대로 내보낸다. */
  education_level?: EducationLevel;
  marital_status?: MaritalStatus;
  has_children?: boolean;
}

export interface CreateConsentInput {
  consent_type: ConsentType;
  notice_version: string;
  granted_by: ConsentGrantedBy;
  /** DISCLOSURE_REALNAME(인물 단위 실명 유지 동의)일 때만 채운다 — 그 외 동의는
   * 사용자 단위라 비워 둔다(backend/app/schemas/consent.py 참조). */
  character_id?: string;
}

export interface UpdateProfileInput {
  name?: string;
  birth_year?: number;
  hometown?: string;
  education_level?: EducationLevel;
  marital_status?: MaritalStatus;
  has_children?: boolean;
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
