"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { session } from "@/lib/auth/session";

/** 로그인 토큰이 없으면 진입 화면으로 돌려보낸다. 대시보드 하위 페이지들의
 * 공통 게이트(app/dashboard/layout.tsx)로 쓴다. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!session.getAccessToken()) {
      router.replace("/");
      return;
    }
    setChecked(true);
  }, [router]);

  if (!checked) return null;
  return <>{children}</>;
}
