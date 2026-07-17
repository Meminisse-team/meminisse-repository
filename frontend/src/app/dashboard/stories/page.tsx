"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
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
  // 이제 "현재 페이지에 보일 7개"만 담는다 — 전체 목록을 받아 클라이언트에서
  // 잘라내던 방식(stories.slice(...))은 페이지네이션이 UI에만 있고 실제 조회는
  // 항상 전체였던 원인이라 제거했다(2026-07-17). 총 개수는 별도 total state로.
  const [stories, setStories] = useState<StoryCard[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // 전체를 한 번에 받지 않고 현재 페이지 분량만 서버에서 받는다(2026-07-16 —
  // 이야기를 많이 나눈 사용자의 첫 로드가 수 초씩 걸리던 문제). 페이지를 넘기면
  // 그때 그 페이지를 요청하고, 폴링도 현재 페이지만 다시 받는다.
  const refresh = useCallback(() => {
    // 이전 시도에서 남은 에러 메시지를 지우지 않으면, 새로고침이 실제로 성공해도
    // 화면엔 옛 에러 문구가 그대로 남아 "안 눌리는 것처럼" 보인다(2026-07-15 피드백).
    setError(null);
    return storiesApi
      .list({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
      .then((res) => {
        setStories(res.items);
        setTotal(res.total);
      })
      .catch(() => setError("이야기를 불러오지 못했어요."));
  }, [page]);

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

  function startEdit(story: StoryCard) {
    setEditingId(story.session_id);
    setDraft(story.prose);
    setSaveError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setSaveError(null);
  }

  // 타이핑할 때마다가 아니라 이 저장 버튼을 눌렀을 때만 호출된다 — 매 저장이
  // 이벤트 재추출(Solar 구조화 호출)을 트리거하므로(story_service.update_session_prose)
  // 연타 낭비를 막기 위해 서버가 쿨다운(429)도 함께 둔다(2026-07-15 검토).
  function saveEdit(sessionId: string) {
    setSaving(true);
    setSaveError(null);
    storiesApi
      .updateProse(sessionId, draft)
      .then((updated) => {
        setStories((current) =>
          current.map((s) => (s.session_id === sessionId ? updated : s))
        );
        setEditingId(null);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 429) {
          setSaveError("너무 자주 저장했어요. 잠시 후 다시 시도해주세요.");
        } else {
          setSaveError("저장하지 못했어요. 잠시 후 다시 시도해주세요.");
        }
      })
      .finally(() => setSaving(false));
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // 목록이 새로고침되며 개수가 줄어들 수 있다(예: 마지막 페이지를 보던 중 데이터가
  // 바뀜) — 그런 경우 현재 페이지가 범위를 벗어나지 않도록 보정한다. 이 보정
  // 자체가 setPage를 호출해 refresh를 다시 트리거하므로(page가 refresh의 의존성),
  // 서버가 이미 돌려준 stories를 다시 자르는 게 아니라 새 offset으로 재요청한다.
  useEffect(() => {
    setPage((current) => Math.min(current, totalPages));
  }, [totalPages]);

  const pageStories = stories;

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
      <p className="mb-3 -mt-4 text-sm text-black/40">
        방금 나눈 이야기는 정리되는 데 잠시 시간이 걸려요. 잠깐 기다리시면 자동으로 나타납니다.
      </p>
      {/* '나의 이야기'(session_prose)와 최종 자서전 원고(챕터 집필)를 사용자가 혼동하지
      않도록 명확히 구분해서 알려준다 — 여기 있는 글은 실제로 하신 말씀을 사실 그대로
      정리만 한 것이지, 자서전 문체로 다시 쓰는 AI가 아직 개입하지 않았다는 걸 강조한다
      (2026-07-16 피드백 — 이 화면의 산문이 매끄럽게 읽혀서 최종본으로 오인할 수 있음). */}
      <div className="mb-6 rounded-2xl border border-black/10 bg-black/[0.02] p-4">
        <p className="text-sm leading-relaxed text-black/60">
          여기 담긴 글은 나눈 대화를 사실 그대로 정리한 기록이에요. 문장만 다듬었을 뿐
          없는 내용을 더하거나 지어내지 않았어요 — 아직 자서전 문체로 다시 쓰는 AI는
          거치지 않은, 있는 그대로의 사실 정리입니다. 지금 쓰고 있는 자서전은{" "}
          <Link href="/dashboard/autobiography" className="underline underline-offset-2 hover:text-black">
            자서전 집필
          </Link>
          에서, 완성된 자서전은{" "}
          <Link href="/dashboard/bookshelf" className="underline underline-offset-2 hover:text-black">
            나의 책장
          </Link>
          에서 확인하실 수 있어요.
        </p>
      </div>

      {loading && <p className="text-black/50">불러오는 중...</p>}
      {error && <p className="text-black/50">{error}</p>}
      {!loading && !error && stories.length === 0 && (
        <p className="text-black/50">아직 나눈 이야기가 없어요. &apos;오늘의 대화&apos;에서 시작해보세요.</p>
      )}

      <div className="flex flex-col gap-5">
        {pageStories.map((story) => {
          const isEditing = editingId === story.session_id;
          return (
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

              {story.is_generating ? (
                // 세션은 끝났지만 산문 재조립(Celery)이 아직 안 끝난 상태 — 대화가
                // 유실된 게 아니라 정상적으로 처리 중임을 보여주는 임시 셀. 폴링
                // (POLL_INTERVAL_MS)이 돌다가 완성되면 이 자리가 실제 카드로 바뀐다.
                <p className="mt-3 animate-pulse text-base leading-relaxed text-black/40">
                  이야기를 정리하고 있어요...
                </p>
              ) : isEditing ? (
                <div className="mt-3 flex flex-col gap-2">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={6}
                    className="w-full resize-y rounded-xl border border-black/20 p-3 text-base leading-relaxed text-black focus:border-black/40 focus:outline-none"
                  />
                  {saveError && <p className="text-sm text-red-600">{saveError}</p>}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => saveEdit(story.session_id)}
                      disabled={saving || draft.trim().length === 0}
                      className="rounded-full bg-black px-4 py-1.5 text-sm text-white disabled:opacity-50"
                    >
                      {saving ? "저장 중..." : "저장"}
                    </button>
                    <button
                      type="button"
                      onClick={cancelEdit}
                      disabled={saving}
                      className="rounded-full px-4 py-1.5 text-sm text-black/50 hover:text-black disabled:opacity-50"
                    >
                      취소
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <p className="mt-3 whitespace-pre-wrap text-base leading-relaxed text-black/70">
                    {story.prose}
                  </p>
                  <button
                    type="button"
                    onClick={() => startEdit(story)}
                    className="mt-2 text-sm text-black/40 underline-offset-4 hover:text-black hover:underline"
                  >
                    이 이야기 수정하기
                  </button>
                </>
              )}
            </article>
          );
        })}
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
