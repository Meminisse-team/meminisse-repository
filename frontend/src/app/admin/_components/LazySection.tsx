"use client";

import { useState } from "react";

/**
 * 관리자 대시보드 섹션 공용 래퍼 — 페이지 진입 시 자동으로 불러오지 않고,
 * "불러오기" 버튼을 눌러야만 load()를 호출한다. 대시보드에 조회 대상이
 * 늘어나면서(세션 목록 2종 + 유저 검색 + DB 열람 + 감사 로그 + 애플리케이션
 * 로그) 진입할 때마다 전부 한꺼번에 로드하면 불필요한 조회가 많아진다는
 * 피드백으로 도입했다.
 */
export function LazySection<T>({
  title,
  description,
  load,
  children,
}: {
  title: string;
  description?: string;
  load: () => Promise<T>;
  children: (data: T, reload: () => void) => React.ReactNode;
}) {
  const [state, setState] = useState<
    | { status: "idle" }
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "loaded"; data: T }
  >({ status: "idle" });

  const handleLoad = () => {
    setState({ status: "loading" });
    load()
      .then((data) => setState({ status: "loaded", data }))
      .catch(() => setState({ status: "error", message: "불러오지 못했어요." }));
  };

  return (
    <section>
      <div className="mb-1 flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-black">{title}</h2>
        <button
          type="button"
          onClick={handleLoad}
          disabled={state.status === "loading"}
          className="shrink-0 rounded-full border border-black/10 px-4 py-1.5 text-sm text-black/60 disabled:opacity-40"
        >
          {state.status === "loading"
            ? "불러오는 중..."
            : state.status === "loaded"
              ? "새로고침"
              : "불러오기"}
        </button>
      </div>
      {description && <p className="mb-4 text-sm text-black/40">{description}</p>}
      {state.status === "error" && <p className="text-base text-black/50">{state.message}</p>}
      {state.status === "loaded" && children(state.data, handleLoad)}
    </section>
  );
}
