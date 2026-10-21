/** Signup credentials stay in memory only; a reload restarts onboarding. */
export interface SignupDraft { email: string; password: string; name: string; }
let current: SignupDraft | null = null;
export const signupDraft = {
  set(draft: SignupDraft) { current = draft; },
  get(): SignupDraft | null { return current; },
  clear() { current = null; },
};
