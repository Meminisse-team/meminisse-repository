"use client";

import { useCallback, useEffect, useState } from "react";

import { autobiographiesApi } from "@/lib/api/autobiographies";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type { Autobiography } from "@/types/api";

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

  const refresh = useCallback(async () => {
    if (!user) return;
    setError(null);
    try {
      const list = await autobiographiesApi.listFinished(user.id);
      setBooks(list);
    } catch {
      setError("책장을 불러오지 못했어요.");
    }
  }, [user]);

  useEffect(() => {
    setLoading(true);
    void refresh().finally(() => setLoading(false));
  }, [refresh]);

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
        {books.map((book) => (
          <article key={book.id} className="rounded-2xl border border-black/10 p-6">
            <p className="text-sm text-black/40">
              {new Date(book.updated_at).toLocaleDateString("ko-KR")} 완성
            </p>
            <h2 className="mt-2 text-lg font-semibold text-black">{book.title ?? "제목 없음"}</h2>
            {book.book_synopsis && (
              <p className="mt-2 text-sm leading-relaxed text-black/60">{book.book_synopsis}</p>
            )}
            <div className="mt-4 flex gap-4">
              {book.pdf_url && (
                <a
                  href={book.pdf_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm text-black underline underline-offset-4 hover:text-black/70"
                >
                  PDF 열기
                </a>
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
        ))}
      </div>
    </main>
  );
}
