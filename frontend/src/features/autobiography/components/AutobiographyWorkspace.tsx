"use client";
import { useAutobiography } from "../hooks/useAutobiography";
import { NeedsConsolidate } from "./NeedsConsolidate";
import { ProgressGate } from "./ProgressGate";
import { NoTocYet } from "./NoTocYet";
import { TocSelection } from "./TocSelection";
import { ChapterProgress } from "./ChapterProgress";
import { FinalManuscript } from "./FinalManuscript";
export function AutobiographyWorkspace() {
  const { autobiography, setAutobiography, chapters, setChapters, loading, consolidateTriggered, finalizeTriggered, pdfTriggered, chaptersWriteTriggered, busy, acknowledgedChapterIds, setAcknowledgedChapterIds, rewritingChapters, error, selecting, handleConsolidate, handleGenerateToc, handleRegenerateToc, handleSelectToc, handleWriteAll, handleRewriteChapter, handleFinalize, handleGeneratePdf } = useAutobiography();
  if (loading) {
    return (
      <main aria-busy={busy} className="px-6 pb-10 pt-14">
        <p role="status" className="text-black/50">불러오는 중...</p>
      </main>
    );
  }

  if (!autobiography) {
    return (
      <main aria-busy={busy} className="px-6 pb-10 pt-14">
        <p className="text-black/50">{error ?? "자서전 정보를 찾을 수 없어요."}</p>
      </main>
    );
  }

  const candidates = autobiography.toc_data?.candidates ?? [];
  const selectedIndex = autobiography.toc_data?.selected_candidate_index ?? null;
  const chaptersAllWritten = chapters.length > 0 && chapters.every((c) => c.content !== null);

  return (
    <main aria-busy={busy} className="px-6 pb-10 pt-14">
      <div role="status" aria-live="polite" className="sr-only">{busy ? "자서전 작업을 진행하고 있습니다." : "자서전 화면을 사용할 수 있습니다."}</div>
      <h1 className="mb-8 font-serif-kr text-2xl text-black">자서전 집필</h1>

      {error && (
        <p role="alert" className="mb-6 rounded-md bg-red-50 px-4 py-3 text-base text-red-700">{error}</p>
      )}

      {autobiography.final_content ? (
        <FinalManuscript
          autobiography={autobiography}
          chapters={chapters}
          busy={busy}
          pdfTriggered={pdfTriggered}
          rewritingChapterIds={new Set(rewritingChapters.keys())}
          onGeneratePdf={handleGeneratePdf}
          onAutobiographyChange={setAutobiography}
          onChapterContentSaved={(chapterId, content) =>
            setChapters((prev) => prev.map((c) => (c.id === chapterId ? { ...c, content } : c)))
          }
          onRewrite={handleRewriteChapter}
          onRegenerateToc={handleRegenerateToc}
        />
      ) : selectedIndex !== null ? (
        <ChapterProgress
          autobiographyId={autobiography.id}
          chapters={chapters}
          selectedCandidate={selectedIndex !== null ? (candidates[selectedIndex] ?? null) : null}
          allWritten={chaptersAllWritten}
          busy={busy}
          finalizeTriggered={finalizeTriggered}
          writeTriggered={chaptersWriteTriggered}
          acknowledgedChapterIds={acknowledgedChapterIds}
          rewritingChapterIds={new Set(rewritingChapters.keys())}
          onAcknowledge={(chapterId) =>
            setAcknowledgedChapterIds((current) => new Set(current).add(chapterId))
          }
          onChapterContentSaved={(chapterId, content) => {
            setChapters((prev) => prev.map((c) => (c.id === chapterId ? { ...c, content } : c)));
            setAcknowledgedChapterIds((current) => new Set(current).add(chapterId));
          }}
          onRewrite={handleRewriteChapter}
          onWriteAll={handleWriteAll}
          onFinalize={handleFinalize}
          onRegenerateToc={handleRegenerateToc}
        />
      ) : candidates.length > 0 ? (
        <TocSelection candidates={candidates} selecting={selecting} onSelect={handleSelectToc} />
      ) : autobiography.status === "in_progress" &&
        autobiography.completed_session_count < autobiography.progress.minimum_sessions ? (
        <ProgressGate completedCount={autobiography.completed_session_count} policy={autobiography.progress} />
      ) : autobiography.status === "in_progress" ? (
        <NeedsConsolidate
          busy={busy}
          triggered={consolidateTriggered}
          onConsolidate={handleConsolidate}
        />
      ) : (
        <NoTocYet busy={busy} onGenerate={handleGenerateToc} />
      )}
    </main>
  );
}
