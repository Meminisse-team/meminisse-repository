"use client";

import { PdfDownloadButton } from "./PdfDownloadButton";
import { Button } from "@/components/ui/Button";
import type { Autobiography, ChapterDraft } from "@/types/api";
import { joinChaptersPreview } from "../utils/joinChaptersPreview";
import { FinalChapterItem } from "./FinalChapterItem";
import { PhotoPlacementPanel } from "./PhotoPlacementPanel";

export function FinalManuscript({
  autobiography,
  chapters,
  busy,
  pdfTriggered,
  rewritingChapterIds,
  onGeneratePdf,
  onAutobiographyChange,
  onChapterContentSaved,
  onRewrite,
  onRegenerateToc,
}: {
  autobiography: Autobiography;
  chapters: ChapterDraft[];
  busy: boolean;
  pdfTriggered: boolean;
  rewritingChapterIds: Set<string>;
  onGeneratePdf: () => void;
  onAutobiographyChange: (updated: Autobiography) => void;
  onChapterContentSaved: (chapterId: string, content: string) => void;
  onRewrite: (chapter: ChapterDraft) => void;
  onRegenerateToc: () => void;
}) {
  const pdfUrl = autobiography.pdf_url;
  return (
    <article className="flex flex-col gap-6">
      <h2 className="font-serif-kr text-xl text-black">{autobiography.title ?? "제목 없음"}</h2>

      {!pdfUrl && (
        <PhotoPlacementPanel
          autobiography={autobiography}
          chapters={chapters}
          onSaved={onAutobiographyChange}
        />
      )}

      <div className="rounded-2xl border border-black/10 p-6">
        {pdfUrl ? (
          <PdfDownloadButton autobiographyId={autobiography.id} />
        ) : (
          <>
            <Button onClick={onGeneratePdf} disabled={busy || pdfTriggered} className="w-full">
              {pdfTriggered ? "책으로 만드는 중..." : busy ? "시작하는 중..." : "책으로 만들기"}
            </Button>
            {pdfTriggered && (
              <p className="mt-3 text-sm text-black/40">
                국판(A5) 크기로 조판하고 있어요. 이 화면을 열어두면 완료되는 대로 자동으로 갱신돼요.
              </p>
            )}
          </>
        )}
      </div>

      {chapters.length > 0 ? (
        <ol className="flex flex-col gap-4">
          {chapters.map((chapter) => (
            <FinalChapterItem
              key={chapter.id}
              autobiographyId={autobiography.id}
              chapter={chapter}
              rewriting={rewritingChapterIds.has(chapter.id)}
              onSaved={(content) => {
                onChapterContentSaved(chapter.id, content);
                onAutobiographyChange({
                  ...autobiography,
                  final_content: joinChaptersPreview(
                    chapters.map((c) => (c.id === chapter.id ? { ...c, content } : c)),
                  ),
                });
              }}
              onRewrite={() => onRewrite(chapter)}
            />
          ))}
        </ol>
      ) : (
        // chapters를 아직 못 불러온 드문 경우(직접 URL 진입 등)의 최소 폴백 — 편집은
        // 챕터 단위라 여기서는 열람만 제공한다.
        <p className="whitespace-pre-wrap text-base leading-loose text-black/80">
          {autobiography.final_content}
        </p>
      )}

      <div className="border-t border-black/10 pt-4">
        <button
          type="button"
          onClick={onRegenerateToc}
          disabled={busy || rewritingChapterIds.size > 0}
          className="text-sm text-black/40 underline underline-offset-4 hover:text-black/60 disabled:no-underline disabled:opacity-40"
        >
          목차 처음부터 다시 만들기
        </button>
        <p className="mt-1 text-xs text-black/30">
          완성된 책을 포함해 지금까지 쓴 챕터 본문이 전부 사라지고, 새 목차를 고른 뒤
          처음부터 다시 쓰게 돼요.
        </p>
      </div>
    </article>
  );
}
