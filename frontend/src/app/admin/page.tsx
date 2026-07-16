"use client";

import { useEffect, useState } from "react";

import { adminApi } from "@/lib/api/admin";
import type { AdminSession } from "@/types/api";

const SESSION_TYPE_LABEL: Record<string, string> = {
  photo: "사진",
  fixed_question: "고정 질문",
  episode: "에피소드",
};

export default function AdminPage() {
  const [staleSessions, setStaleSessions] = useState<AdminSession[]>([]);
  const [crisisSessions, setCrisisSessions] = useState<AdminSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([adminApi.listStaleSessions(), adminApi.listCrisisSessions()])
      .then(([stale, crisis]) => {
        setStaleSessions(stale);
        setCrisisSessions(crisis);
      })
      .catch(() => setError("관리자 데이터를 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="px-6 pb-10 pt-6">
      <h1 className="mb-8 font-serif-kr text-2xl text-black">관리자 대시보드</h1>

      {loading && <p className="text-black/50">불러오는 중...</p>}
      {error && <p className="text-black/50">{error}</p>}

      {!loading && !error && (
        <div className="flex flex-col gap-8">
          <section>
            <h2 className="mb-1 text-lg font-semibold text-black">처리 지연 세션</h2>
            <p className="mb-4 text-sm text-black/40">
              완료됐지만 10분 넘게 산문 재조립이 끝나지 않은 세션 — Celery 워커가
              내려가 있는 등 처리가 아예 시작되지 못했을 가능성이 있어요.
            </p>
            <SessionList sessions={staleSessions} emptyLabel="처리 지연 세션이 없어요." />
          </section>

          <section>
            <h2 className="mb-1 text-lg font-semibold text-black">위기 대응 로그</h2>
            <p className="mb-4 text-sm text-black/40">
              위기 대응 문구가 발화된 세션이에요. 사후 검토가 필요할 수 있어요.
            </p>
            <SessionList sessions={crisisSessions} emptyLabel="위기 대응 기록이 없어요." />
          </section>
        </div>
      )}
    </main>
  );
}

function SessionList({ sessions, emptyLabel }: { sessions: AdminSession[]; emptyLabel: string }) {
  if (sessions.length === 0) {
    return <p className="text-base text-black/50">{emptyLabel}</p>;
  }
  return (
    <ul className="flex flex-col gap-3">
      {sessions.map((s) => (
        <li key={s.id} className="rounded-2xl border border-black/10 p-4 text-sm">
          <p className="text-black/70">
            {SESSION_TYPE_LABEL[s.session_type] ?? s.session_type} · user {s.user_id.slice(0, 8)}
          </p>
          <p className="mt-1 text-black/40">
            시작 {new Date(s.started_at).toLocaleString("ko-KR")}
            {s.completed_at && ` · 완료 ${new Date(s.completed_at).toLocaleString("ko-KR")}`}
          </p>
        </li>
      ))}
    </ul>
  );
}
