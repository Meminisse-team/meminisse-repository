"use client";

import { useState } from "react";

import { adminApi } from "@/lib/api/admin";
import type { AdminLogService } from "@/types/api";

const SERVICE_OPTIONS: { value: AdminLogService; label: string }[] = [
  { value: "backend", label: "백엔드" },
  { value: "worker", label: "워커" },
  { value: "beat", label: "beat" },
];

export function AppLogSection() {
  const [service, setService] = useState<AdminLogService>("worker");
  const [lines, setLines] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    adminApi
      .getAppLogs(service, 200)
      .then((res) => setLines(res.lines))
      .finally(() => setLoading(false));
  };

  return (
    <section>
      <h2 className="mb-1 text-lg font-semibold text-black">애플리케이션 로그</h2>
      <p className="mb-4 text-sm text-black/40">
        백엔드/워커/beat 프로세스의 최근 로그 라인이에요. 아직 로그 파일이 없으면
        빈 화면으로 보일 수 있어요.
      </p>
      <div className="mb-4 flex gap-2">
        <select
          value={service}
          onChange={(e) => setService(e.target.value as AdminLogService)}
          className="rounded-xl border border-black/10 px-3 py-2 text-sm"
        >
          {SERVICE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-full border border-black/10 px-4 py-2 text-sm text-black/60 disabled:opacity-40"
        >
          {loading ? "불러오는 중..." : "불러오기"}
        </button>
      </div>

      {lines !== null && (
        <pre className="max-h-96 overflow-auto rounded-xl border border-black/10 bg-black/[0.02] p-3 text-xs leading-relaxed text-black/70">
          {lines.length > 0 ? lines.join("") : "로그가 아직 없어요."}
        </pre>
      )}
    </section>
  );
}
