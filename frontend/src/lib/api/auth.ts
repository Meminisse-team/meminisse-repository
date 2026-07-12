import { apiClient } from "@/lib/api/client";
import type { OAuthProvider, OAuthSyncResponse, TokenResponse, User } from "@/types/api";

export interface LoginInput {
  email: string;
  password: string;
}

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;

/** 소셜 로그인 버튼이 브라우저를 이 URL로 그대로 이동시킨다(fetch 아님 — Supabase가
 * 카카오/구글 동의 화면으로 리다이렉트한 뒤, 다시 이 프로젝트의 /auth/callback으로
 * 세션 토큰과 함께 돌려보낸다). redirectTo는 반드시 Supabase 대시보드
 * (Authentication → URL Configuration → Redirect URLs)에 허용 목록으로 등록돼
 * 있어야 한다 — 안 그러면 Supabase가 리다이렉트 자체를 거부한다. */
export function buildOAuthAuthorizeUrl(provider: OAuthProvider): string {
  if (!SUPABASE_URL) {
    throw new Error("NEXT_PUBLIC_SUPABASE_URL이 설정되지 않았어요(.env.local 확인).");
  }
  const redirectTo = `${window.location.origin}/auth/callback`;
  const params = new URLSearchParams({ provider, redirect_to: redirectTo });
  return `${SUPABASE_URL}/auth/v1/authorize?${params.toString()}`;
}

export const authApi = {
  login: (input: LoginInput) => apiClient.post<TokenResponse>("/api/v1/auth/login", input),
  refresh: (refreshToken: string) =>
    apiClient.post<TokenResponse>("/api/v1/auth/refresh", { refresh_token: refreshToken }),
  me: () => apiClient.get<User>("/api/v1/auth/me"),
  /** 소셜 로그인 콜백 직후 호출 — public.users 프로필이 없으면(최초 로그인)
   * 이메일/표시 이름만으로 만들어준다(backend/app/api/v1/auth.py 참조). */
  oauthSync: () => apiClient.post<OAuthSyncResponse>("/api/v1/auth/oauth-sync"),
};
