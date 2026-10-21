"use client";

import Link from "next/link";

import type { Autobiography } from "@/types/api";
export function ProgressGate({ completedCount, policy }: { completedCount: number; policy: Autobiography["progress"] }) {
  const { minimum_sessions: MIN_COMPLETED_SESSIONS, recommended_sessions: RECOMMENDED_COMPLETED_SESSIONS, progress_total: PROGRESS_TOTAL } = policy;
  const pct = Math.min(100, (completedCount / PROGRESS_TOTAL) * 100);
  const minPct = (MIN_COMPLETED_SESSIONS / PROGRESS_TOTAL) * 100;
  const recommendedPct = (RECOMMENDED_COMPLETED_SESSIONS / PROGRESS_TOTAL) * 100;

  return (
    <div className="flex flex-col items-start gap-6 rounded-2xl border border-black/10 p-6">
      <p className="text-lg leading-relaxed text-black">
        아직 자서전을 시작하기엔 나눈 이야기가 조금 부족해요. 조금 더 이야기를 들려주시면
        훨씬 풍성한 자서전을 만들 수 있어요.
      </p>

      <div className="w-full">
        <div className="relative h-2 w-full rounded-full bg-black/10">
          <div
            className="h-2 rounded-full bg-black transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
          <div
            className="absolute top-0 h-2 w-px bg-white/70"
            style={{ left: `${minPct}%` }}
            aria-hidden="true"
          />
          <div
            className="absolute top-0 h-2 w-px bg-white/70"
            style={{ left: `${recommendedPct}%` }}
            aria-hidden="true"
          />
        </div>
        <div className="mt-2 flex justify-between text-xs text-black/40">
          <span>{completedCount}개 답변</span>
          <span>
            시작 가능 {MIN_COMPLETED_SESSIONS} · 권장 {RECOMMENDED_COMPLETED_SESSIONS} · 총{" "}
            {PROGRESS_TOTAL}
          </span>
        </div>
      </div>

      <Link
        href="/dashboard"
        className="text-base text-black/50 underline underline-offset-4 hover:text-black/70"
      >
        오늘의 대화로 돌아가기
      </Link>
    </div>
  );
}
