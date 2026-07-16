"use client";

import { LazySection } from "@/app/admin/_components/LazySection";
import { AppLogSection } from "@/app/admin/_components/AppLogSection";
import { DbViewerSection } from "@/app/admin/_components/DbViewerSection";
import { UserLookupSection } from "@/app/admin/_components/UserLookupSection";
import { adminApi } from "@/lib/api/admin";
import type { AdminAuditLog, AdminSession } from "@/types/api";

const SESSION_TYPE_LABEL: Record<string, string> = {
  photo: "사진",
  fixed_question: "고정 질문",
  episode: "에피소드",
};

export default function AdminPage() {
  return (
    <main className="px-6 pb-10 pt-6">
      <h1 className="mb-8 font-serif-kr text-2xl text-black">관리자 대시보드</h1>

      <div className="flex flex-col gap-8">
        <LazySection
          title="처리 지연 세션"
          description="완료됐지만 10분 넘게 산문 재조립이 끝나지 않은 세션 — Celery 워커가
            내려가 있는 등 처리가 아예 시작되지 못했을 가능성이 있어요. 대량
            처리 중(예: 테스트 데이터 시딩)이라면 이 숫자가 자연스럽게 줄어드는
            중일 수 있어요 — 다시 눌러서 확인해보세요."
          load={adminApi.listStaleSessions}
        >
          {(sessions: AdminSession[]) => (
            <>
              {sessions.length > 0 && (
                <span className="mb-2 inline-block rounded-full bg-amber-100 px-2.5 py-0.5 text-sm font-medium text-amber-800">
                  {sessions.length}개 남음
                </span>
              )}
              <SessionList sessions={sessions} emptyLabel="처리 지연 세션이 없어요." />
            </>
          )}
        </LazySection>

        <LazySection
          title="위기 대응 로그"
          description="위기 대응 문구가 발화된 세션이에요. 사후 검토가 필요할 수 있어요."
          load={adminApi.listCrisisSessions}
        >
          {(sessions: AdminSession[]) => (
            <SessionList sessions={sessions} emptyLabel="위기 대응 기록이 없어요." />
          )}
        </LazySection>

        <UserLookupSection />

        <DbViewerSection />

        <LazySection
          title="관리자 감사 로그"
          description="관리자가 언제 어떤 열람/수정을 했는지 기록한 로그예요."
          load={() => adminApi.listAuditLogs(50, 0)}
        >
          {(logs: AdminAuditLog[]) => <AuditLogList logs={logs} />}
        </LazySection>

        <AppLogSection />
      </div>
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

function AuditLogList({ logs }: { logs: AdminAuditLog[] }) {
  if (logs.length === 0) {
    return <p className="text-base text-black/50">감사 로그가 없어요.</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {logs.map((log) => (
        <li key={log.id} className="rounded-xl border border-black/10 p-3 text-sm">
          <p className="text-black/70">{log.action}</p>
          <p className="mt-1 text-black/40">
            {new Date(log.created_at).toLocaleString("ko-KR")}
            {log.target_user_id && ` · user ${log.target_user_id.slice(0, 8)}`}
            {log.target_session_id && ` · session ${log.target_session_id.slice(0, 8)}`}
          </p>
        </li>
      ))}
    </ul>
  );
}
