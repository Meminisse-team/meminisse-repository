"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { usersApi } from "@/lib/api/users";
import { session } from "@/lib/auth/session";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type { ConsentRecord } from "@/types/api";

const STAGE_LABEL: Record<string, string> = {
  onboarding: "온보딩 중",
  interview: "대화 진행 중",
  publishing: "자서전 제작 중",
  published: "자서전 완성",
};

/** 이 탭은 더미 없이 실제 백엔드(GET /auth/me, GET /users/{id}/consents)와 연동한다 —
 * 이미 완성된 인증 엔드포인트를 그대로 검증해볼 수 있는 화면이라 스캐폴딩
 * 단계에서도 실제 연동을 우선했다. */
export default function ProfilePage() {
  const router = useRouter();
  const { user, loading, error } = useCurrentUser();
  const [consents, setConsents] = useState<ConsentRecord[]>([]);

  useEffect(() => {
    if (!user) return;
    usersApi
      .listConsents(user.id)
      .then(setConsents)
      .catch(() => setConsents([]));
  }, [user]);

  function handleLogout() {
    session.clear();
    router.replace("/");
  }

  return (
    <main className="px-6 pb-10 pt-14">
      <h1 className="mb-8 font-serif-kr text-2xl text-black">내 정보</h1>

      {loading && <p className="text-black/50">불러오는 중...</p>}
      {error && <p className="text-black/50">{error}</p>}

      {user && (
        <div className="flex flex-col gap-6">
          <section className="rounded-2xl border border-black/10 p-6">
            <dl className="flex flex-col gap-3 text-base">
              <Row label="이름" value={user.name} />
              <Row label="이메일" value={user.email} />
              <Row label="출생연도" value={user.birth_year ? `${user.birth_year}년` : "미입력"} />
              <Row label="고향" value={user.hometown ?? "미입력"} />
              <Row label="진행 단계" value={STAGE_LABEL[user.current_stage] ?? user.current_stage} />
            </dl>
          </section>

          <section className="rounded-2xl border border-black/10 p-6">
            <p className="mb-3 text-sm text-black/40">동의 내역</p>
            {consents.length === 0 ? (
              <p className="text-base text-black/50">기록된 동의 내역이 없어요.</p>
            ) : (
              <ul className="flex flex-col gap-2 text-base text-black/70">
                {consents.map((c) => (
                  <li key={c.id}>
                    {new Date(c.granted_at).toLocaleDateString("ko-KR")} · {c.consent_type}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {user.role === "admin" && (
            <Link
              href="/admin"
              className="rounded-2xl border border-black/10 p-6 text-base text-black transition-colors hover:bg-black/5"
            >
              관리자 대시보드 →
            </Link>
          )}

          <Button variant="secondary" onClick={handleLogout}>
            로그아웃
          </Button>
        </div>
      )}
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-black/5 pb-3">
      <dt className="text-black/40">{label}</dt>
      <dd className="text-black">{value}</dd>
    </div>
  );
}
