"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type { Autobiography } from "@/types/api";

const POLL_INTERVAL_MS = 4000;

/** "나의 책장" — 완성한(final_content가 채워진) 자서전을 전부 모아 보여준다.
 * 유저당 자서전이 여러 버전 가능해지면서(2026-07-17, migration 015) "자서전
 * 집필"은 항상 지금 쓰고 있는 하나만 가리키게 됐고, 지난 버전들은 여기서만
 * 확인할 수 있다. 나의 이야기 화면과 달리 폴링·페이지네이션은 두지 않았다 —
 * 버전 수가 세션 수만큼 많아지지는 않을 것으로 보이고, 필요해지면 나중에
 * stories/page.tsx의 패턴을 그대로 가져오면 된다. */
export default function BookshelfPage() {
  const { user } = useCurrentUser();
  const [books, setBooks] = useState<Autobiography[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // "PDF로 만들기"를 누른 책 id — 완성된 자서전은 finalize 시점에 PDF가 자동으로
  // 만들어지지 않으므로(별도 조판 작업), 책장에서도 직접 트리거할 수 있어야 한다.
  const [pdfTriggeredIds, setPdfTriggeredIds] = useState<Set<string>>(new Set());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    if (!user) return;
    setError(null);
    try {
      const list = await autobiographiesApi.listFinished(user.id);
      setBooks(list);
      // pdf_url이 채워진 책은 더 이상 폴링 대상이 아니다.
      setPdfTriggeredIds((prev) => {
        if (prev.size === 0) return prev;
        const stillPending = new Set(
          list.filter((b) => prev.has(b.id) && !b.pdf_url).map((b) => b.id),
        );
        return stillPending.size === prev.size ? prev : stillPending;
      });
    } catch {
      setError("책장을 불러오지 못했어요.");
    }
  }, [user]);

  useEffect(() => {
    // loading 초기값이 이미 true라 여기서 다시 setLoading(true)를 부를 필요가 없다
    // (react-hooks/set-state-in-effect).
    void refresh().finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (pdfTriggeredIds.size === 0) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (!pollRef.current) {
      pollRef.current = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [pdfTriggeredIds, refresh]);

  async function handleGeneratePdf(bookId: string) {
    setPdfTriggeredIds((prev) => new Set(prev).add(bookId));
    try {
      await autobiographiesApi.generatePdf(bookId);
    } catch {
      setError("PDF를 만들지 못했어요. 잠시 후 다시 시도해주세요.");
      setPdfTriggeredIds((prev) => {
        const next = new Set(prev);
        next.delete(bookId);
        return next;
      });
    }
  }

  return (
    <main className="px-6 pb-10 pt-14">
      <h1 className="mb-8 font-serif-kr text-2xl text-black">나의 책장</h1>

      {loading && <p className="text-black/50">불러오는 중...</p>}
      {error && <p className="text-black/50">{error}</p>}
      {!loading && !error && books.length === 0 && (
        <p className="text-black/50">
          아직 완성한 자서전이 없어요. 자서전 집필에서 첫 번째 책을 완성해보세요.
        </p>
      )}

      <div className="flex flex-col gap-5">
        {books.map((book) => {
          const pdfTriggered = pdfTriggeredIds.has(book.id);
          return (
            <article key={book.id} className="rounded-2xl border border-black/10 p-6">
              <p className="text-sm text-black/40">
                {new Date(book.updated_at).toLocaleDateString("ko-KR")} 완성
              </p>
              <h2 className="mt-2 text-lg font-semibold text-black">{book.title ?? "제목 없음"}</h2>
              {book.book_synopsis && (
                <p className="mt-2 text-sm leading-relaxed text-black/60">{book.book_synopsis}</p>
              )}

              <div className="mt-4">
                {book.pdf_url ? (
                  <a
                    href={book.pdf_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-block rounded-full bg-black px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-black/80"
                  >
                    PDF 열기 / 다운로드
                  </a>
                ) : (
                  <>
                    <Button
                      onClick={() => void handleGeneratePdf(book.id)}
                      disabled={pdfTriggered}
                      className="px-5 py-2 text-sm"
                    >
                      {pdfTriggered ? "책으로 만드는 중..." : "PDF로 만들기"}
                    </Button>
                    {pdfTriggered && (
                      <p className="mt-2 text-sm text-black/40">
                        국판(A5) 크기로 조판하고 있어요. 이 화면을 열어두면 완료되는 대로 자동으로
                        갱신돼요.
                      </p>
                    )}
                  </>
                )}
              </div>

              {book.final_content && (
                <p className="mt-4 whitespace-pre-wrap text-base leading-relaxed text-black/70">
                  {book.final_content.length > 300
                    ? `${book.final_content.slice(0, 300)}...`
                    : book.final_content}
                </p>
              )}
            </article>
          );
        })}
      </div>
    </main>
  );
}
