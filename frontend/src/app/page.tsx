"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { authApi, buildOAuthAuthorizeUrl } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { session } from "@/lib/auth/session";
import { signupDraft } from "@/lib/auth/signupDraft";
import type { OAuthProvider } from "@/types/api";

type Mode = "login" | "signup";

export default function EntryPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleOAuthClick(provider: OAuthProvider) {
    try {
      window.location.href = buildOAuthAuthorizeUrl(provider);
    } catch {
      setError("소셜 로그인을 지금은 이용할 수 없어요. 잠시 후 다시 시도해주세요.");
    }
  }

  async function handleLogin() {
    const { access_token, refresh_token } = await authApi.login({ email, password });
    session.setTokens(access_token, refresh_token);
    router.push("/dashboard");
  }

  function handleSignupStart() {
    // 실제 계정 생성(POST /api/v1/users)은 온보딩 마지막 단계에서 한다 —
    // 백엔드에 프로필 수정 API가 없어, 출생연도·고향까지 다 모은 뒤 한 번에
    // 보내는 편이 낫다(lib/auth/signupDraft.ts 참조).
    signupDraft.set({ email, password, name });
    router.push("/greeting");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") {
        await handleLogin();
      } else {
        handleSignupStart();
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("이메일 또는 비밀번호를 다시 확인해주세요.");
      } else {
        setError("잠시 후 다시 시도해주세요.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-16 px-6 py-16">
      <h1 className="font-serif-kr text-4xl tracking-wide text-black">Meminisse</h1>

      <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-5">
        <div className="mb-2 flex justify-center gap-6 text-lg">
          <button
            type="button"
            onClick={() => setMode("login")}
            className={mode === "login" ? "font-semibold text-black" : "text-black/35"}
          >
            로그인
          </button>
          <button
            type="button"
            onClick={() => setMode("signup")}
            className={mode === "signup" ? "font-semibold text-black" : "text-black/35"}
          >
            회원가입
          </button>
        </div>

        {mode === "signup" && (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="이름"
            required
            className="w-full border-b border-black/20 bg-transparent px-1 py-3 text-lg outline-none placeholder:text-black/35 focus:border-black"
          />
        )}
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="이메일"
          required
          className="w-full border-b border-black/20 bg-transparent px-1 py-3 text-lg outline-none placeholder:text-black/35 focus:border-black"
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="비밀번호 (8자 이상)"
          minLength={8}
          required
          className="w-full border-b border-black/20 bg-transparent px-1 py-3 text-lg outline-none placeholder:text-black/35 focus:border-black"
        />

        {error && <p className="text-center text-base text-black/60">{error}</p>}

        <Button type="submit" disabled={submitting} className="mt-4 w-full">
          {mode === "login" ? "로그인" : "다음"}
        </Button>

        <div className="my-2 flex items-center gap-4 text-sm text-black/35">
          <span className="h-px flex-1 bg-black/10" />
          또는
          <span className="h-px flex-1 bg-black/10" />
        </div>

        <button
          type="button"
          onClick={() => handleOAuthClick("kakao")}
          className="w-full rounded-lg bg-[#FEE500] px-6 py-3 text-lg font-medium text-black/85 transition-opacity hover:opacity-90"
        >
          카카오로 시작하기
        </button>
        <button
          type="button"
          onClick={() => handleOAuthClick("google")}
          className="w-full rounded-lg border border-black/15 bg-white px-6 py-3 text-lg font-medium text-black transition-colors hover:border-black/30"
        >
          Google로 시작하기
        </button>
      </form>
    </main>
  );
}
