"use client";

import { useCallback, useEffect, useState } from "react";

import { storiesApi } from "@/lib/api/stories";
import type { StoryCard } from "@/types/api";

// 대화 종료 후 카드가 여기 나타나기까지는 백엔드가 비동기로(Celery) 산문 재조립·
// 이벤트 추출·임베딩을 순서대로 처리하는 시간이 걸린다(수십 초~길게는 2분 가까이,
// 로컬 NLI 모델을 처음 로드할 때 특히 오래 걸림) — "바로 연동이 안 된다"는
// 피드백(2026-07-15)에 대응해, 페이지가 떠 있는 동안 주기적으로 자동 새로고침한다.
const POLL_INTERVAL_MS = 8000;

const PAGE_SIZE = 7; // 한 창에 보여줄 산문 개수
const PAGE_WINDOW = 5; // 페이지 번호를 한 번에 몇 개씩 보여줄지

export default function StoriesPage() {
  const [stories, setStories] = useState<StoryCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const refresh = useCallback(() => {
    // 이전 시도에서 남은 에러 메시지를 지우지 않으면, 새로고침이 실제로 성공해도
    // 화면엔 옛 에러 문구가 그대로 남아 "안 눌리는 것처럼" 보인다(2026-07-15 피드백).
    setError(null);
    return storiesApi
      .list()
      .then(setStories)
      .catch(() => setError("이야기를 불러오지 못했어요."));
  }, []);

  useEffect(() => {
    setLoading(true);
    void refresh().finally(() => setLoading(false));

    const intervalId = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [refresh]);

  function handleManualRefresh() {
    // 버튼을 눌러도 아무 반응이 없어 보였던 이유 중 하나 — 클릭했다는 시각적
    // 피드백이 전혀 없었다. 폴링과 별개로 수동 클릭 시엔 "새로고침 중..."을 보여준다.
    setRefreshing(true);
    void refresh().finally(() => setRefreshing(false));
  }

  const totalPages = Math.max(1, Math.ceil(stories.length / PAGE_SIZE));
  // 목록이 새로고침되며 개수가 줄어들 수 있다(예: 마지막 페이지를 보던 중 데이터가
  // 바뀜) — 그런 경우 현재 페이지가 범위를 벗어나지 않도록 보정한다.
  useEffect(() => {
    setPage((current) => Math.min(current, totalPages));
  }, [totalPages]);

  const pageStories = stories.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  // 페이지 번호를 PAGE_WINDOW개씩 블록으로 나눠 보여준다(1~5, 6~10, ...).
  const blockIndex = Math.floor((page - 1) / PAGE_WINDOW);
  const windowStart = blockIndex * PAGE_WINDOW + 1;
  const windowEnd = Math.min(totalPages, windowStart + PAGE_WINDOW - 1);
  const pageNumbers = Array.from(
    { length: Math.max(0, windowEnd - windowStart + 1) },
    (_, i) => windowStart + i
  );
  const hasPrevBlock = windowStart > 1;
  const hasNextBlock = windowEnd < totalPages;

  return (
    <main className="px-6 pb-10 pt-14">
      <div className="mb-8 flex items-center justify-between">
        <h1 className="font-serif-kr text-2xl text-black">나의 이야기</h1>
        <button
          type="button"
          onClick={handleManualRefresh}
          disabled={refreshing}
          className="text-sm text-black/50 underline-offset-4 hover:text-black hover:underline disabled:opacity-50"
        >
          {refreshing ? "새로고침 중..." : "새로고침"}
        </button>
      </div>
      <p className="mb-6 -mt-4 text-sm text-black/40">
        방금 나눈 이야기는 정리되는 데 잠시 시간이 걸려요. 잠깐 기다리시면 자동으로 나타납니다.
      </p>

      {loading && <p className="text-black/50">불러오는 중...</p>}
      {error && <p className="text-black/50">{error}</p>}
      {!loading && !error && stories.length === 0 && (
        <p className="text-black/50">아직 나눈 이야기가 없어요. &apos;오늘의 대화&apos;에서 시작해보세요.</p>
      )}

      <div className="flex flex-col gap-5">
        {pageStories.map((story) => (
          <article key={story.session_id} className="rounded-2xl border border-black/10 p-6">
            {story.completed_at && (
              <p className="text-sm text-black/40">
                {new Date(story.completed_at).toLocaleDateString("ko-KR")}
              </p>
            )}
            {/* 제목 = 이 세션에서 실제로 물었던 질문 그 자체(무엇에 대한 이야기인지
            바로 알 수 있게, 2026-07-15 피드백). */}
            <h2 className="mt-2 text-lg font-semibold text-black">{story.title}</h2>
            {/* 부제 = 재조립된 산문으로부터 재추출한 요약 라벨. */}
            {story.subtitle && <p className="mt-1 text-sm text-black/50">{story.subtitle}</p>}
            <p className="mt-3 text-base leading-relaxed text-black/70">{story.prose}</p>
          </article>
        ))}
      </div>

      {/* 총 페이지가 1개뿐이면(산문이 7개 이하) 페이지네이션 자체를 보여줄 필요가 없다. */}
      {totalPages > 1 && (
        <nav className="mt-8 flex items-center justify-center gap-2">
          {hasPrevBlock && (
            <button
              type="button"
              onClick={() => setPage(windowStart - 1)}
              className="rounded-full px-3 py-1.5 text-sm text-black/50 hover:text-black"
            >
              이전
            </button>
          )}
          {pageNumbers.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPage(p)}
              aria-current={p === page ? "page" : undefined}
              className={`h-8 w-8 rounded-full text-sm ${
                p === page ? "bg-black text-white" : "text-black/60 hover:bg-black/5"
              }`}
            >
              {p}
            </button>
          ))}
          {hasNextBlock && (
            <button
              type="button"
              onClick={() => setPage(windowEnd + 1)}
              className="rounded-full px-3 py-1.5 text-sm text-black/50 hover:text-black"
            >
              다음
            </button>
          )}
        </nav>
      )}
    </main>
  );
}
