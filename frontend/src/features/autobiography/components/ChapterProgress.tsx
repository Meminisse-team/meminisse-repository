"use client";

import { Button } from "@/components/ui/Button";
import type { ChapterDraft, TocCandidate } from "@/types/api";
import { ChapterReviewItem } from "./ChapterReviewItem";

export function ChapterProgress({
  autobiographyId,
  chapters,
  selectedCandidate,
  allWritten,
  busy,
  finalizeTriggered,
  writeTriggered,
  acknowledgedChapterIds,
  rewritingChapterIds,
  onAcknowledge,
  onChapterContentSaved,
  onRewrite,
  onWriteAll,
  onFinalize,
  onRegenerateToc,
}: {
  autobiographyId: string;
  chapters: ChapterDraft[];
  selectedCandidate: TocCandidate | null;
  allWritten: boolean;
  busy: boolean;
  finalizeTriggered: boolean;
  writeTriggered: boolean;
  acknowledgedChapterIds: Set<string>;
  rewritingChapterIds: Set<string>;
  onAcknowledge: (chapterId: string) => void;
  onChapterContentSaved: (chapterId: string, content: string) => void;
  onRewrite: (chapter: ChapterDraft) => void;
  onWriteAll: () => void;
  onFinalize: () => void;
  onRegenerateToc: () => void;
}) {
  const parts = selectedCandidate?.parts ?? [];
  const hasParts = parts.length > 1;
  const partIndexByChapterIndex = new Map(
    selectedCandidate?.chapters.map((c) => [c.chapter_index, c.part_index]) ?? [],
  );

  return (
    <div className="flex flex-col gap-6">
      {hasParts ? (
        <div className="flex flex-col gap-6">
          {parts.map((part) => (
            <div key={part.part_index}>
              <p className="mb-3 text-base font-medium text-black/70">
                {part.part_index}부. {part.part_title}
              </p>
              <ol className="flex flex-col gap-3">
                {chapters
                  .filter((chapter) => partIndexByChapterIndex.get(chapter.chapter_index) === part.part_index)
                  .map((chapter) => (
                    <ChapterReviewItem
                      key={chapter.id}
                      autobiographyId={autobiographyId}
                      chapter={chapter}
                      writing={writeTriggered && chapter.content === null}
                      rewriting={rewritingChapterIds.has(chapter.id)}
                      acknowledged={acknowledgedChapterIds.has(chapter.id)}
                      onAcknowledge={() => onAcknowledge(chapter.id)}
                      onSaved={(content) => onChapterContentSaved(chapter.id, content)}
                      onRewrite={() => onRewrite(chapter)}
                    />
                  ))}
              </ol>
            </div>
          ))}
        </div>
      ) : (
        <ol className="flex flex-col gap-3">
          {chapters.map((chapter) => (
            <ChapterReviewItem
              key={chapter.id}
              autobiographyId={autobiographyId}
              chapter={chapter}
              writing={writeTriggered && chapter.content === null}
              rewriting={rewritingChapterIds.has(chapter.id)}
              acknowledged={acknowledgedChapterIds.has(chapter.id)}
              onAcknowledge={() => onAcknowledge(chapter.id)}
              onSaved={(content) => onChapterContentSaved(chapter.id, content)}
              onRewrite={() => onRewrite(chapter)}
            />
          ))}
        </ol>
      )}

      {!allWritten && (
        <>
          <Button onClick={onWriteAll} disabled={busy}>
            {busy ? "집필을 시작하는 중..." : "각 장 집필 시작"}
          </Button>
          <p className="text-sm text-black/40">
            집필에는 시간이 걸려요. 이 화면을 열어두면 완료되는 대로 자동으로 갱신돼요.
          </p>
        </>
      )}
      {allWritten && !finalizeTriggered && (
        // 다시 쓰는 챕터가 남아 있는 동안 최종본을 만들면 옛 본문이 섞여 들어가므로 막는다.
        <Button onClick={onFinalize} disabled={busy || rewritingChapterIds.size > 0}>
          {busy ? "최종본을 만드는 중..." : "최종본 만들기"}
        </Button>
      )}
      {allWritten && finalizeTriggered && (
        <p className="text-sm text-black/40">최종 원고를 다듬고 있어요. 잠시만 기다려주세요...</p>
      )}

      <div className="mt-4 border-t border-black/10 pt-4">
        <button
          type="button"
          onClick={onRegenerateToc}
          disabled={busy || rewritingChapterIds.size > 0}
          className="text-sm text-black/40 underline underline-offset-4 hover:text-black/60 disabled:no-underline disabled:opacity-40"
        >
          목차 처음부터 다시 만들기
        </button>
        <p className="mt-1 text-xs text-black/30">
          지금까지 쓴 챕터 본문이 전부 사라지고, 새 목차를 고른 뒤 처음부터 다시 쓰게 돼요.
        </p>
      </div>
    </div>
  );
}
