"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** 대시보드 하위 탭(사진첩/나의 이야기/내 정보)과 자서전 화면은 BottomNav에 "홈" 탭이
 * 없어(기획상 탭은 3개뿐, BottomNav.tsx 참조) 대시보드 메인으로 돌아갈 방법이 없었다.
 * 대시보드 메인(/dashboard) 자체는 이미 큰 로고가 곧 홈 링크라 이 헤더가 필요 없으므로,
 * 그 경로에서는 아무것도 렌더링하지 않는다. */
export function DashboardHeader() {
  const pathname = usePathname();
  if (pathname === "/dashboard") return null;

  return (
    <header className="sticky top-0 z-40 flex items-center bg-white/95 px-6 py-4 backdrop-blur">
      <Link
        href="/dashboard"
        className="flex items-center gap-2 text-base text-black/50 transition-colors hover:text-black"
      >
        <span aria-hidden>←</span>
        홈
      </Link>
    </header>
  );
}
