/**
 * 진입화면(이메일/비밀번호/이름)에서 온보딩 완료(출생연도/고향/동의)까지 이어지는
 * 임시 가입 정보. 백엔드에 프로필 수정 API가 없어 회원 생성(POST /api/v1/users)을
 * 온보딩 마지막 단계까지 미루기로 했으므로, 그 사이 값을 들고 있어야 한다.
 * 탭을 닫으면 사라져도 되는 값이라 sessionStorage를 쓴다(로그인 세션과는 별개).
 */

const DRAFT_KEY = "meminisse.signup_draft";

export interface SignupDraft {
  email: string;
  password: string;
  name: string;
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export const signupDraft = {
  set(draft: SignupDraft) {
    if (!isBrowser()) return;
    window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  },
  get(): SignupDraft | null {
    if (!isBrowser()) return null;
    const raw = window.sessionStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as SignupDraft;
    } catch {
      return null;
    }
  },
  clear() {
    if (!isBrowser()) return;
    window.sessionStorage.removeItem(DRAFT_KEY);
  },
};
