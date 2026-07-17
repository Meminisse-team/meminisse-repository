"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/dashboard/photos", label: "사진첩" },
  { href: "/dashboard/bookshelf", label: "나의 책장" },
  { href: "/dashboard/stories", label: "나의 이야기" },
  { href: "/dashboard/profile", label: "내 정보" },
] as const;

/** 대시보드 하위 탭 전용 네비게이션. 대시보드 메인('오늘의 대화' + 로고)은
 * 탭이 아니라 로고를 눌러 돌아가는 홈으로 취급한다. 기획엔 원래 3개 탭(사진첩/
 * 나의 이야기/내 정보)만 있었는데, 완성한 자서전을 모아 보는 "나의 책장"이
 * 생기면서 2026-07-17에 4번째 탭으로 추가됐다(사진첩과 나의 이야기 사이). */
export function BottomNav() {
  const pathname = usePathname();

  return (
    <nav className="sticky bottom-0 flex border-t border-black/10 bg-white">
      {TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={`flex-1 py-4 text-center text-base transition-colors ${
              active ? "font-semibold text-black" : "text-black/40"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
