/**
 * 로그인 세션(Supabase Auth 토큰) 저장소. 이 스캐폴딩 단계에서는 localStorage에 그대로
 * 두는 가장 단순한 방식을 쓴다 — httpOnly 쿠키 기반으로 옮기는 것은 실제 배포 전
 * 보안 강화 작업으로 남겨둔다(현재는 XSS에 취약할 수 있음을 인지할 것).
 */

const ACCESS_TOKEN_KEY = "meminisse.access_token";
const REFRESH_TOKEN_KEY = "meminisse.refresh_token";
const USER_ID_KEY = "meminisse.user_id";

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export const session = {
  setTokens(accessToken: string, refreshToken: string) {
    if (!isBrowser()) return;
    window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    window.localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  },
  getAccessToken(): string | null {
    if (!isBrowser()) return null;
    return window.localStorage.getItem(ACCESS_TOKEN_KEY);
  },
  getRefreshToken(): string | null {
    if (!isBrowser()) return null;
    return window.localStorage.getItem(REFRESH_TOKEN_KEY);
  },
  setUserId(id: string) {
    if (!isBrowser()) return;
    window.localStorage.setItem(USER_ID_KEY, id);
  },
  getUserId(): string | null {
    if (!isBrowser()) return null;
    return window.localStorage.getItem(USER_ID_KEY);
  },
  clear() {
    if (!isBrowser()) return;
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    window.localStorage.removeItem(USER_ID_KEY);
  },
};
