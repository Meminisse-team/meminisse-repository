/**
 * 소셜 로그인(카카오/구글) 최초 로그인 직후 "프로필 완성이 필요하다"는 표시.
 *
 * 이메일/비밀번호 가입(signupDraft.ts)과의 차이: OAuth는 제공자가 동의하는 순간
 * 계정이 이미 만들어져 있어(auth/callback에서 oauthSync 완료), 이 시점엔 이미
 * 로그인된 상태다 — draft처럼 "아직 계정이 없는 정보"가 아니라 "이미 로그인된
 * 사용자의 생년/고향/동의가 비어있다"는 사실만 온보딩 페이지에 전달하면 된다.
 */

const PENDING_KEY = "meminisse.oauth_pending_profile";

export interface OAuthPendingProfile {
  name: string;
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export const oauthPendingProfile = {
  set(profile: OAuthPendingProfile) {
    if (!isBrowser()) return;
    window.sessionStorage.setItem(PENDING_KEY, JSON.stringify(profile));
  },
  get(): OAuthPendingProfile | null {
    if (!isBrowser()) return null;
    const raw = window.sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as OAuthPendingProfile;
    } catch {
      return null;
    }
  },
  clear() {
    if (!isBrowser()) return;
    window.sessionStorage.removeItem(PENDING_KEY);
  },
};
