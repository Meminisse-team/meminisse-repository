"use client";

import { useState } from "react";

import { adminApi } from "@/lib/api/admin";
import type { AdminDbRow, AdminDbTable } from "@/types/api";

const TABLE_OPTIONS: { value: AdminDbTable; label: string }[] = [
  { value: "users", label: "유저" },
  { value: "sessions", label: "세션" },
  { value: "events", label: "이벤트" },
  { value: "autobiographies", label: "자서전" },
  { value: "chapter_drafts", label: "챕터 초안" },
];

const PAGE_SIZE = 50;

export function DbViewerSection() {
  const [table, setTable] = useState<AdminDbTable>("users");
  const [rows, setRows] = useState<AdminDbRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadedTable, setLoadedTable] = useState<AdminDbTable | null>(null);

  const load = (nextTable: AdminDbTable, offset: number) => {
    setLoading(true);
    adminApi
      .listDbTable(nextTable, PAGE_SIZE, offset)
      .then((data) => {
        setRows((prev) => (offset === 0 ? data : [...prev, ...data]));
        setLoadedTable(nextTable);
      })
      .finally(() => setLoading(false));
  };

  return (
    <section>
      <h2 className="mb-1 text-lg font-semibold text-black">DB 열람</h2>
      <p className="mb-4 text-sm text-black/40">
        핵심 테이블을 구조화된 형태로 읽기 전용 조회합니다(임의 SQL 실행 불가).
      </p>
      <div className="mb-4 flex gap-2">
        <select
          value={table}
          onChange={(e) => setTable(e.target.value as AdminDbTable)}
          className="rounded-xl border border-black/10 px-3 py-2 text-sm"
        >
          {TABLE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => load(table, 0)}
          disabled={loading}
          className="rounded-full border border-black/10 px-4 py-2 text-sm text-black/60 disabled:opacity-40"
        >
          {loading ? "불러오는 중..." : "불러오기"}
        </button>
      </div>

      {loadedTable !== null && (
        <>
          <RowTable rows={rows} />
          {rows.length > 0 && rows.length % PAGE_SIZE === 0 && (
            <button
              type="button"
              onClick={() => load(loadedTable, rows.length)}
              disabled={loading}
              className="mt-3 rounded-full border border-black/10 px-4 py-1.5 text-sm text-black/60 disabled:opacity-40"
            >
              더 보기
            </button>
          )}
        </>
      )}
    </section>
  );
}

function RowTable({ rows }: { rows: AdminDbRow[] }) {
  if (rows.length === 0) {
    return <p className="text-base text-black/50">결과가 없어요.</p>;
  }
  const columns = Object.keys(rows[0]);
  return (
    <div className="overflow-x-auto rounded-xl border border-black/10">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-black/10 bg-black/[0.02]">
            {columns.map((col) => (
              <th key={col} className="whitespace-nowrap px-3 py-2 font-medium text-black/60">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-black/5 last:border-0">
              {columns.map((col) => (
                <td key={col} className="max-w-[240px] truncate px-3 py-2 text-black/70">
                  {formatCell(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
