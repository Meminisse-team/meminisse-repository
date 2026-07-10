"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/dashboard/photos", label: "사진첩" },
  { href: "/dashboard/stories", label: "나의 이야기" },
  { href: "/dashboard/profile", label: "내 정보" },
] as const;

/** 대시보드 하위 3개 탭 전용 네비게이션. 대시보드 메인('오늘의 대화' + 로고)은
 * 탭이 아니라 로고를 눌러 돌아가는 홈으로 취급한다(기획에 명시된 탭은 3개뿐). */
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
