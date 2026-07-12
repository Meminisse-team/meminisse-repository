"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { authApi } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { oauthPendingProfile } from "@/lib/auth/oauthPendingProfile";
import { session } from "@/lib/auth/session";

/** 소셜 로그인(카카오/구글) 후 Supabase가 여기로 돌려보낸다. Supabase는 SPA용
 * 암묵적 흐름(implicit flow)을 쓰므로 토큰이 쿼리스트링이 아니라 URL 프래그먼트
 * (#access_token=...&refresh_token=...)로 온다 — 서버 컴포넌트는 프래그먼트를
 * 아예 못 보므로(브라우저가 서버에 전송하지 않음) 이 페이지는 반드시 클라이언트
 * 컴포넌트여야 한다. */
export default function OAuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const hash = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : window.location.hash;
    const params = new URLSearchParams(hash);
    const accessToken = params.get("access_token");
    const refreshToken = params.get("refresh_token");
    const oauthError = params.get("error_description") || params.get("error");

    if (oauthError) {
      setError("소셜 로그인이 취소되었거나 실패했어요.");
      return;
    }
    if (!accessToken || !refreshToken) {
      setError("로그인 정보를 확인하지 못했어요. 다시 시도해주세요.");
      return;
    }

    session.setTokens(accessToken, refreshToken);

    authApi
      .oauthSync()
      .then(({ user, is_new }) => {
        session.setUserId(user.id);
        if (is_new) {
          // 소셜 로그인은 이름을 이미 제공자로부터 받았으니 온보딩에서 다시 묻지
          // 않는다 — 생년/고향/동의만 마저 받으면 된다(app/onboarding/page.tsx
          // 참조, oauthPendingProfile이 있으면 이름 입력 스텝을 건너뛴다).
          oauthPendingProfile.set({ name: user.name });
          router.replace("/onboarding");
        } else {
          router.replace("/dashboard");
        }
      })
      .catch((err) => {
        session.clear();
        if (err instanceof ApiError) {
          setError("로그인 처리 중 문제가 생겼어요. 잠시 후 다시 시도해주세요.");
        } else {
          setError("알 수 없는 오류가 발생했어요.");
        }
      });
  }, [router]);

  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 px-6 py-16 text-center">
      {error ? (
        <>
          <p className="text-lg text-black">{error}</p>
          <button
            type="button"
            onClick={() => router.replace("/")}
            className="text-base text-black/60 underline underline-offset-4"
          >
            처음 화면으로 돌아가기
          </button>
        </>
      ) : (
        <p className="text-lg text-black/50">로그인하는 중...</p>
      )}
    </main>
  );
}
