"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";

/** 대시보드 3탭(BottomNav.tsx)과 무관한 별도 영역이라 dashboard/layout.tsx를 쓰지
 * 않는다. role=admin이 아니면 대시보드로 돌려보낸다 — 백엔드도 동일하게
 * AdminUserDep(require_admin)으로 403을 던지지만, 이 리다이렉트는 UX용이고
 * 실제 접근 통제는 백엔드가 최종 책임진다. */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, loading } = useCurrentUser();

  useEffect(() => {
    if (!loading && user && user.role !== "admin") {
      router.replace("/dashboard");
    }
  }, [loading, user, router]);

  return (
    <RequireAuth>
      <div className="flex min-h-screen flex-col">
        <header className="sticky top-0 z-40 flex items-center bg-white/95 px-6 py-4 backdrop-blur">
          <Link
            href="/dashboard/profile"
            className="flex items-center gap-2 text-base text-black/50 transition-colors hover:text-black"
          >
            <span aria-hidden>←</span>
            내 정보
          </Link>
        </header>
        {!loading && user && user.role === "admin" && <div className="flex-1">{children}</div>}
      </div>
    </RequireAuth>
  );
}
