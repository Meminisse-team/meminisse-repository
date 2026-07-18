"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import { mediaApi } from "@/lib/api/media";
import { ApiError } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type {
  Autobiography,
  ChapterDraft,
  MediaAsset,
  PhotoPlacement,
  PhotoPlacementSlot,
  TocCandidate,
} from "@/types/api";

const POLL_INTERVAL_MS = 4000;

// 자서전 집필 진행률 게이트 — 백엔드 상수(app/services/autobiography_service.py:
// MIN_COMPLETED_SESSIONS_FOR_AUTOBIOGRAPHY 등)와 반드시 같은 값으로 유지할 것.
const MIN_COMPLETED_SESSIONS = 50;
const RECOMMENDED_COMPLETED_SESSIONS = 80;
const PROGRESS_TOTAL = 130;

/** 이 챕터에 확인이 필요하다고 표시된(팩트체크+근거검증) 항목 총 개수 — 0이면 표시할 게 없다. */
function chapterFlagCount(chapter: ChapterDraft): number {
  return (
    (chapter.factcheck_report?.flags.length ?? 0) + (chapter.groundedness_report?.flags.length ?? 0)
  );
}

/** 자서전 진행 상태에 따라 이야기 정리(Phase 3) → 목차 만들기 → 목차 선택 → 챕터 집필
 * 진행 → 최종본 열람까지 한 화면에서 이어서 보여준다(기획안 5절 흐름 그대로, 별도
 * 페이지로 쪼개지 않는다 — 시니어 사용자가 "지금 자서전이 어디까지 왔는지"를 한 곳에서
 * 확인할 수 있게 하기 위함).
 *
 * 이야기 정리(consolidate)·챕터 집필(write)·최종본 윤문(finalize)은 전부 Celery로
 * 큐잉만 하고 즉시 202를 반환하는 비동기 작업이라, 완료 여부는 이 화면이 주기적으로
 * 다시 조회(폴링)해서 확인한다. 목차 생성/선택만 동기 응답(200)이다. */
export default function AutobiographyPage() {
  const { user } = useCurrentUser();
  const [autobiography, setAutobiography] = useState<Autobiography | null>(null);
  const [chapters, setChapters] = useState<ChapterDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [consolidateTriggered, setConsolidateTriggered] = useState(false);
  const [finalizeTriggered, setFinalizeTriggered] = useState(false);
  const [pdfTriggered, setPdfTriggered] = useState(false);
  // "각 장 집필 시작"을 눌렀는지 — 개별 챕터에 "생성 중" 신호가 없어서(백엔드에
  // is_generating 같은 필드가 없음), 이 화면 방문 동안 사용자가 실제로 집필을
  // 트리거했는지를 프론트에서만 기억해 미완료 챕터에 "집필하고 있어요..." 표시를
  // 보여준다(새로고침하면 초기화되지만, Promise.all로 전 챕터를 한 번에 큐잉하는
  // 현재 방식과 맞물려 이 화면에 머무는 동안은 항상 정확하다).
  const [chaptersWriteTriggered, setChaptersWriteTriggered] = useState(false);
  // "확인 필요 N곳" 배지를 사용자가 직접 읽어보고 넘겨도 되겠다고 표시한 챕터
  // id 집합. 백엔드 데이터(factcheck_report/groundedness_report)는 그대로 두고
  // 화면에서만 감추는 것이라 새로고침하면 다시 나타난다 — 영구적으로 지우려면
  // 별도 백엔드 필드가 필요해 이번 범위에서는 프론트 상태로만 구현했다.
  const [acknowledgedChapterIds, setAcknowledgedChapterIds] = useState<Set<string>>(new Set());
  // "이 챕터 다시 쓰기"를 누른 챕터들. 다시 쓰는 챕터는 본문이 이미 있어서
  // (content !== null) 완료를 content로 감지할 수 없다 — 트리거 시점의
  // updated_at을 기억해 두고, 폴링으로 받은 updated_at이 달라지면 완료로 본다.
  const [rewritingChapters, setRewritingChapters] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (tick: () => void) => {
      if (!pollRef.current) {
        pollRef.current = setInterval(tick, POLL_INTERVAL_MS);
      }
    },
    [],
  );

  const load = useCallback(async () => {
    if (!user) return;
    const bio = await autobiographiesApi.get(user.id);
    setAutobiography(bio);
    const hasSelectedToc = bio.toc_data?.selected_candidate_index != null;
    if (hasSelectedToc) {
      // final_content가 생긴 뒤에도 챕터 목록은 계속 쓴다 — 수록 사진 배치 단계가
      // "몇 장에 넣을지" 선택지를 그리는 재료다(PhotoPlacementPanel 참조).
      const list = await autobiographiesApi.listChapters(bio.id);
      setChapters(list);
      if (list.length > 0 && list.every((c) => c.content !== null)) {
        setChaptersWriteTriggered(false);
      }
      // 다시 쓰기가 끝난 챕터(updated_at이 트리거 시점과 달라짐)는 목록에서 뺀다.
      setRewritingChapters((prev) => {
        if (prev.size === 0) return prev;
        const next = new Map(prev);
        for (const chapter of list) {
          const triggeredAt = next.get(chapter.id);
          if (triggeredAt !== undefined && chapter.updated_at !== triggeredAt) {
            next.delete(chapter.id);
          }
        }
        return next.size === prev.size ? prev : next;
      });
    } else {
      setChapters([]);
    }
    if (bio.final_content) setFinalizeTriggered(false);
    if (bio.status !== "in_progress") setConsolidateTriggered(false);
    if (bio.pdf_url) setPdfTriggered(false);
    return bio;
  }, [user]);

  useEffect(() => {
    if (!user) return;
    // loading 초기값이 true라 여기서 다시 setLoading(true)를 부를 필요가 없다 —
    // effect 안의 직접 setState는 불필요한 리렌더를 만든다(react-hooks/set-state-in-effect).
    load()
      .catch(() => setError("자서전 정보를 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, [user, load]);

  useEffect(() => stopPolling, [stopPolling]);

  // 챕터 집필 중이거나(내용 없는 챕터가 남아있음) 최종본 윤문을 기다리는 동안만 폴링한다.
  useEffect(() => {
    const waitingOnChapters = chapters.length > 0 && chapters.some((c) => c.content === null);
    const chaptersAllWritten = chapters.length > 0 && chapters.every((c) => c.content !== null);
    const waitingOnFinalize = chaptersAllWritten && finalizeTriggered && !autobiography?.final_content;
    const waitingOnConsolidate = consolidateTriggered && autobiography?.status === "in_progress";
    const waitingOnPdf = pdfTriggered && !autobiography?.pdf_url;
    const waitingOnRewrite = rewritingChapters.size > 0;

    if (waitingOnChapters || waitingOnFinalize || waitingOnConsolidate || waitingOnPdf || waitingOnRewrite) {
      startPolling(() => void load());
    } else {
      stopPolling();
    }
  }, [
    chapters,
    autobiography,
    finalizeTriggered,
    consolidateTriggered,
    pdfTriggered,
    rewritingChapters,
    load,
    startPolling,
    stopPolling,
  ]);

  async function handleConsolidate() {
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      await autobiographiesApi.consolidate(user.id);
      setConsolidateTriggered(true);
      startPolling(() => void load());
    } catch {
      setError("이야기를 정리하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateToc() {
    if (!autobiography) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await autobiographiesApi.generateToc(autobiography.id);
      setAutobiography(updated);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("아직 목차를 만들 준비가 안 됐어요. 대화를 조금 더 나눠주세요.");
      } else {
        setError("목차를 만들지 못했어요. 잠시 후 다시 시도해주세요.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleSelectToc(index: number) {
    if (!autobiography) return;
    setSelecting(index);
    setError(null);
    try {
      const updated = await autobiographiesApi.selectToc(autobiography.id, index);
      setAutobiography(updated);
      const list = await autobiographiesApi.listChapters(updated.id);
      setChapters(list);
    } catch {
      setError("목차를 선택하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSelecting(null);
    }
  }

  async function handleWriteAll() {
    if (!autobiography) return;
    const unwritten = chapters.filter((c) => c.content === null);
    if (unwritten.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await Promise.all(unwritten.map((c) => autobiographiesApi.writeChapter(autobiography.id, c.id)));
      setChaptersWriteTriggered(true);
      startPolling(() => void load());
    } catch {
      setError("챕터 집필을 시작하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRewriteChapter(chapter: ChapterDraft) {
    if (!autobiography) return;
    if (
      !window.confirm(
        `${chapter.chapter_index}장을 처음부터 다시 쓸까요? 현재 본문은 새로 쓴 내용으로 대체돼요.`
      )
    ) {
      return;
    }
    setError(null);
    try {
      await autobiographiesApi.writeChapter(autobiography.id, chapter.id);
      setRewritingChapters((prev) => new Map(prev).set(chapter.id, chapter.updated_at));
      startPolling(() => void load());
    } catch {
      setError("챕터 다시 쓰기를 시작하지 못했어요. 잠시 후 다시 시도해주세요.");
    }
  }

  async function handleFinalize() {
    if (!autobiography) return;
    // "확인했어요"를 누른 챕터는 사용자가 이미 검토했다는 뜻이므로 이 경고에서 뺀다.
    const flaggedCount = chapters
      .filter((c) => !acknowledgedChapterIds.has(c.id))
      .reduce((sum, c) => sum + chapterFlagCount(c), 0);
    if (
      flaggedCount > 0 &&
      !window.confirm(
        `아직 확인이 필요하다고 표시된 부분이 ${flaggedCount}곳 있어요. 그래도 최종본을 만들까요?`
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await autobiographiesApi.finalize(autobiography.id);
      setFinalizeTriggered(true);
      startPolling(() => void load());
    } catch {
      setError("최종본을 만들지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGeneratePdf() {
    if (!autobiography) return;
    setBusy(true);
    setError(null);
    try {
      await autobiographiesApi.generatePdf(autobiography.id);
      setPdfTriggered(true);
      startPolling(() => void load());
    } catch {
      setError("PDF를 만들지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <main className="px-6 pb-10 pt-14">
        <p className="text-black/50">불러오는 중...</p>
      </main>
    );
  }

  if (!autobiography) {
    return (
      <main className="px-6 pb-10 pt-14">
        <p className="text-black/50">{error ?? "자서전 정보를 찾을 수 없어요."}</p>
      </main>
    );
  }

  const candidates = autobiography.toc_data?.candidates ?? [];
  const selectedIndex = autobiography.toc_data?.selected_candidate_index ?? null;
  const chaptersAllWritten = chapters.length > 0 && chapters.every((c) => c.content !== null);

  return (
    <main className="px-6 pb-10 pt-14">
      <h1 className="mb-8 font-serif-kr text-2xl text-black">자서전 집필</h1>

      {error && <p className="mb-6 text-base text-black/60">{error}</p>}

      {autobiography.final_content ? (
        <FinalManuscript
          autobiography={autobiography}
          chapters={chapters}
          busy={busy}
          pdfTriggered={pdfTriggered}
          onGeneratePdf={handleGeneratePdf}
          onAutobiographyChange={setAutobiography}
        />
      ) : selectedIndex !== null ? (
        <ChapterProgress
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
          onRewrite={handleRewriteChapter}
          onWriteAll={handleWriteAll}
          onFinalize={handleFinalize}
        />
      ) : candidates.length > 0 ? (
        <TocSelection candidates={candidates} selecting={selecting} onSelect={handleSelectToc} />
      ) : autobiography.status === "in_progress" &&
        autobiography.completed_session_count < MIN_COMPLETED_SESSIONS ? (
        <ProgressGate completedCount={autobiography.completed_session_count} />
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

/** CSS만으로 그리는 작은 회전 스피너 — 이 프로젝트엔 별도 스피너 컴포넌트가
 * 없어서, "정리하고 있어요"/"집필하고 있어요" 같은 문구 앞에 붙여 "지금 뭔가
 * 진행 중"이라는 걸 정적인 텍스트 한 줄보다 눈에 띄게 만든다. */
function Spinner() {
  return (
    <span
      className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-black/20 border-t-black"
      aria-hidden="true"
    />
  );
}

function NeedsConsolidate({
  busy,
  triggered,
  onConsolidate,
}: {
  busy: boolean;
  triggered: boolean;
  onConsolidate: () => void;
}) {
  return (
    <div className="flex flex-col items-start gap-6 rounded-2xl border border-black/10 p-6">
      <p className="text-lg leading-relaxed text-black">
        지금까지 나눈 이야기를 하나로 모아 정리할게요. 정리가 끝나면 목차를 만들 수 있어요.
      </p>
      {triggered ? (
        <div className="flex items-center gap-2">
          <Spinner />
          <p className="animate-pulse text-sm text-black/40">
            이야기를 정리하고 있어요. 이 화면을 열어두면 자동으로 다음 화면(목차 만들기)으로
            넘어가요...
          </p>
        </div>
      ) : (
        <Button onClick={onConsolidate} disabled={busy}>
          {busy ? "시작하는 중..." : "이야기 정리하기"}
        </Button>
      )}
    </div>
  );
}

/** 완료된 세션(재조립 산문)이 최소 기준(MIN_COMPLETED_SESSIONS) 미만이면 "이야기
 * 정리하기"조차 보여주지 않고 이 진행률 화면을 대신 보여준다(2026-07-17 제품
 * 결정 — 재료가 너무 적으면 자서전 자체가 부실해지므로 아예 시작을 막는다). */
function ProgressGate({ completedCount }: { completedCount: number }) {
  const pct = Math.min(100, (completedCount / PROGRESS_TOTAL) * 100);
  const minPct = (MIN_COMPLETED_SESSIONS / PROGRESS_TOTAL) * 100;
  const recommendedPct = (RECOMMENDED_COMPLETED_SESSIONS / PROGRESS_TOTAL) * 100;

  return (
    <div className="flex flex-col items-start gap-6 rounded-2xl border border-black/10 p-6">
      <p className="text-lg leading-relaxed text-black">
        아직 자서전을 시작하기엔 나눈 이야기가 조금 부족해요. 조금 더 이야기를 들려주시면
        훨씬 풍성한 자서전을 만들 수 있어요.
      </p>

      <div className="w-full">
        <div className="relative h-2 w-full rounded-full bg-black/10">
          <div
            className="h-2 rounded-full bg-black transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
          <div
            className="absolute top-0 h-2 w-px bg-white/70"
            style={{ left: `${minPct}%` }}
            aria-hidden="true"
          />
          <div
            className="absolute top-0 h-2 w-px bg-white/70"
            style={{ left: `${recommendedPct}%` }}
            aria-hidden="true"
          />
        </div>
        <div className="mt-2 flex justify-between text-xs text-black/40">
          <span>{completedCount}개 답변</span>
          <span>
            시작 가능 {MIN_COMPLETED_SESSIONS} · 권장 {RECOMMENDED_COMPLETED_SESSIONS} · 총{" "}
            {PROGRESS_TOTAL}
          </span>
        </div>
      </div>

      <Link
        href="/dashboard"
        className="text-base text-black/50 underline underline-offset-4 hover:text-black/70"
      >
        오늘의 대화로 돌아가기
      </Link>
    </div>
  );
}

function NoTocYet({ busy, onGenerate }: { busy: boolean; onGenerate: () => void }) {
  return (
    <div className="flex flex-col items-start gap-6 rounded-2xl border border-black/10 p-6">
      <p className="text-lg leading-relaxed text-black">
        지금까지 나눈 이야기를 바탕으로 자서전의 목차를 만들어볼 수 있어요.
      </p>
      <Link
        href="/dashboard/autobiography/customize"
        className="text-base text-black/50 underline underline-offset-4 hover:text-black/70"
      >
        먼저 말투와 분위기를 정해볼까요? (선택)
      </Link>
      <Button onClick={onGenerate} disabled={busy}>
        {busy ? "목차 만드는 중..." : "목차 만들기"}
      </Button>
    </div>
  );
}

function TocSelection({
  candidates,
  selecting,
  onSelect,
}: {
  candidates: TocCandidate[];
  selecting: number | null;
  onSelect: (index: number) => void;
}) {
  return (
    <div className="flex flex-col gap-6">
      <p className="text-lg leading-relaxed text-black">
        마음에 드는 목차를 하나 골라주세요. 이 목차를 바탕으로 각 장을 써 내려가요.
      </p>
      {candidates.map((candidate, index) => {
        const parts = candidate.parts ?? [];
        const hasParts = parts.length > 1;
        return (
          <div key={index} className="rounded-2xl border border-black/10 p-6">
            <p className="mb-4 text-sm text-black/40">목차 {index + 1}</p>
            {hasParts ? (
              <div className="flex flex-col gap-4">
                {parts.map((part) => (
                  <div key={part.part_index}>
                    <p className="mb-2 text-sm font-medium text-black/60">
                      {part.part_index}부. {part.part_title}
                    </p>
                    <ol className="flex flex-col gap-2">
                      {candidate.chapters
                        .filter((chapter) => chapter.part_index === part.part_index)
                        .map((chapter) => (
                          <li key={chapter.chapter_index} className="pl-2 text-base text-black">
                            {chapter.chapter_index}장. {chapter.title}
                          </li>
                        ))}
                    </ol>
                  </div>
                ))}
              </div>
            ) : (
              <ol className="flex flex-col gap-2">
                {candidate.chapters.map((chapter) => (
                  <li key={chapter.chapter_index} className="text-base text-black">
                    {chapter.chapter_index}장. {chapter.title}
                  </li>
                ))}
              </ol>
            )}
            <Button
              variant="secondary"
              className="mt-5 w-full"
              disabled={selecting !== null}
              onClick={() => onSelect(index)}
            >
              {selecting === index ? "선택하는 중..." : "이 목차로 진행"}
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function ChapterProgress({
  chapters,
  selectedCandidate,
  allWritten,
  busy,
  finalizeTriggered,
  writeTriggered,
  acknowledgedChapterIds,
  rewritingChapterIds,
  onAcknowledge,
  onRewrite,
  onWriteAll,
  onFinalize,
}: {
  chapters: ChapterDraft[];
  selectedCandidate: TocCandidate | null;
  allWritten: boolean;
  busy: boolean;
  finalizeTriggered: boolean;
  writeTriggered: boolean;
  acknowledgedChapterIds: Set<string>;
  rewritingChapterIds: Set<string>;
  onAcknowledge: (chapterId: string) => void;
  onRewrite: (chapter: ChapterDraft) => void;
  onWriteAll: () => void;
  onFinalize: () => void;
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
                      chapter={chapter}
                      writing={writeTriggered && chapter.content === null}
                      rewriting={rewritingChapterIds.has(chapter.id)}
                      acknowledged={acknowledgedChapterIds.has(chapter.id)}
                      onAcknowledge={() => onAcknowledge(chapter.id)}
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
              chapter={chapter}
              writing={writeTriggered && chapter.content === null}
              rewriting={rewritingChapterIds.has(chapter.id)}
              acknowledged={acknowledgedChapterIds.has(chapter.id)}
              onAcknowledge={() => onAcknowledge(chapter.id)}
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
    </div>
  );
}

const STATUS_LABEL: Record<ChapterDraft["status"], string> = {
  draft: "집필 전",
  reviewed: "집필 완료",
  finalized: "최종 확정",
};

/** 챕터 하나 — 접었다 폈다 하며 본문과, 팩트체크/근거검증에 걸린 부분을 사람이
 * 직접 확인할 수 있게 보여준다. factcheck_report/groundedness_report는 write_chapter가
 * 이미 계산해두지만(autobiography_service.py) 지금까지 이 화면 어디에도 노출되지
 * 않아 사실상 죽은 데이터였다(2026-07-16) — 이 카드가 그걸 실제로 보여주는 첫 자리다.
 *
 * writing: "각 장 집필 시작"을 누른 뒤 아직 본문이 없는 챕터에 대해 true — 백엔드가
 * "생성 중" 신호를 따로 주지 않아(is_generating 필드 없음) 프론트가 트리거 여부로
 * 유추한다(page.tsx의 chaptersWriteTriggered 참조).
 * acknowledged/onAcknowledge: "확인 필요 N곳" 배지를 사용자가 직접 읽어보고 지울 수
 * 있게 하는 프론트 전용 토글 — 백엔드 데이터는 바뀌지 않고 화면에서만 감춘다.
 * rewriting/onRewrite: 이미 집필된 챕터를 처음부터 다시 쓰는 액션(write_chapter가
 * 챕터 단위 멱등이라 같은 API를 재호출하면 된다) — 플래그가 많은 챕터를 사용자가
 * 실제로 "해소"할 수 있는 첫 수단(2026-07-18). */
function ChapterReviewItem({
  chapter,
  writing,
  rewriting,
  acknowledged,
  onAcknowledge,
  onRewrite,
}: {
  chapter: ChapterDraft;
  writing: boolean;
  rewriting: boolean;
  acknowledged: boolean;
  onAcknowledge: () => void;
  onRewrite: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const flagCount = chapterFlagCount(chapter);
  const showFlagBadge = flagCount > 0 && !acknowledged;
  const canExpand = chapter.content !== null;

  return (
    <li className="rounded-2xl border border-black/10 p-5">
      <button
        type="button"
        onClick={() => canExpand && setExpanded((v) => !v)}
        disabled={!canExpand}
        className="flex w-full items-center justify-between gap-4 text-left disabled:cursor-default"
      >
        <span className="min-w-0 flex-1 text-base text-black">
          {chapter.chapter_index}장. {chapter.title ?? "제목 준비 중"}
        </span>
        <span className="flex shrink-0 items-center gap-2 text-sm">
          {showFlagBadge && (
            <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-amber-800">
              확인 필요 {flagCount}곳
            </span>
          )}
          {writing || rewriting ? (
            <span className="flex items-center gap-1.5 text-black/40">
              <Spinner />
              <span className="animate-pulse">
                {rewriting ? "다시 쓰고 있어요..." : "집필하고 있어요..."}
              </span>
            </span>
          ) : (
            <span className="text-black/40">{STATUS_LABEL[chapter.status]}</span>
          )}
          {canExpand && <span className="text-black/30">{expanded ? "▲" : "▼"}</span>}
        </span>
      </button>

      {expanded && chapter.content && (
        <div className="mt-4 flex flex-col gap-4 border-t border-black/10 pt-4">
          <p className="whitespace-pre-wrap text-base leading-relaxed text-black/80">
            {chapter.content}
          </p>
          {showFlagBadge && (
            <div className="flex flex-col gap-2 rounded-xl bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-900">
                아래 부분은 원래 이야기와 다를 수 있어요 — 직접 읽어보고 확인해주세요.
              </p>
              <ul className="flex flex-col gap-2">
                {chapter.groundedness_report?.flags.map((flag, i) => (
                  <li key={`g-${i}`} className="text-sm text-amber-900/80">
                    &ldquo;{flag.sentence}&rdquo;
                  </li>
                ))}
                {chapter.factcheck_report?.flags.map((flag, i) => (
                  <li key={`f-${i}`} className="text-sm text-amber-900/80">
                    &ldquo;{flag.raw_text}&rdquo; — 원래 이야기에서 일치하는 내용을 찾지 못했어요.
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={onAcknowledge}
                className="self-start text-sm text-amber-900 underline underline-offset-4 hover:text-amber-950"
              >
                확인했어요
              </button>
            </div>
          )}
          <button
            type="button"
            onClick={onRewrite}
            disabled={rewriting}
            className="self-start text-sm text-black/50 underline underline-offset-4 hover:text-black/70 disabled:no-underline disabled:opacity-40"
          >
            {rewriting ? "다시 쓰고 있어요..." : "이 챕터 다시 쓰기"}
          </button>
        </div>
      )}
    </li>
  );
}

function FinalManuscript({
  autobiography,
  chapters,
  busy,
  pdfTriggered,
  onGeneratePdf,
  onAutobiographyChange,
}: {
  autobiography: Autobiography;
  chapters: ChapterDraft[];
  busy: boolean;
  pdfTriggered: boolean;
  onGeneratePdf: () => void;
  onAutobiographyChange: (updated: Autobiography) => void;
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
          <a
            href={pdfUrl}
            target="_blank"
            rel="noreferrer"
            className="block w-full rounded-lg bg-black px-6 py-3 text-center text-lg font-medium text-white transition-colors hover:bg-black/80"
          >
            책 PDF 열어보기
          </a>
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

      <p className="whitespace-pre-wrap text-base leading-loose text-black/80">
        {autobiography.final_content}
      </p>
    </article>
  );
}

const SLOT_OPTIONS: { value: PhotoPlacementSlot; label: string }[] = [
  { value: "chapter_top", label: "챕터 첫머리" },
  { value: "full_page_before", label: "챕터 앞 전면 페이지" },
];

/** PDF로 만들기 전에 "어떤 사진을 몇 장 어디에 넣을지"를 고르는 단계(2026-07-16).
 * 기획안 5절의 고정 슬롯 템플릿 원칙에 따라 위치는 자유 배치가 아니라 챕터 +
 * 슬롯(첫머리/전면 페이지) 조합으로만 지정한다. 여기서 고른 사진만 책에 들어간다 —
 * 저장하지 않으면(null) 사진 없이 조판된다(자동 선택 없음, 2026-07-17). */
function PhotoPlacementPanel({
  autobiography,
  chapters,
  onSaved,
}: {
  autobiography: Autobiography;
  chapters: ChapterDraft[];
  onSaved: (updated: Autobiography) => void;
}) {
  const [photos, setPhotos] = useState<MediaAsset[]>([]);
  const [selections, setSelections] = useState<Map<string, PhotoPlacement>>(() => {
    const initial = new Map<string, PhotoPlacement>();
    for (const placement of autobiography.photo_placements ?? []) {
      initial.set(placement.media_asset_id, placement);
    }
    return initial;
  });
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    mediaApi
      .list()
      .then((assets) => {
        if (!cancelled) setPhotos(assets.filter((a) => a.asset_type === "image"));
      })
      .catch(() => {
        if (!cancelled) setError("사진 목록을 불러오지 못했어요.");
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function updateSelection(assetId: string, placement: PhotoPlacement | null) {
    setSelections((prev) => {
      const next = new Map(prev);
      if (placement) {
        next.set(assetId, placement);
      } else {
        next.delete(assetId);
      }
      return next;
    });
    setDirty(true);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const updated = await autobiographiesApi.updatePhotoPlacements(
        autobiography.id,
        Array.from(selections.values()),
      );
      onSaved(updated);
      setDirty(false);
    } catch {
      setError("사진 배치를 저장하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSaving(false);
    }
  }

  // 책에 넣고 싶은 사진이 사진첩에 아직 없을 수 있다 — 이 단계에서 바로 추가
  // 업로드할 수 있게 한다(2026-07-16). 올린 사진은 목록 맨 앞에 나타나고,
  // 체크해서 배치를 지정하면 된다.
  async function handleUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of Array.from(files)) {
        const uploaded = await mediaApi.upload({ file });
        setPhotos((prev) => [uploaded, ...prev]);
      }
    } catch {
      setError("사진을 올리지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  if (!loaded) {
    return (
      <div className="rounded-2xl border border-black/10 p-6">
        <p className="text-sm text-black/40">사진 목록을 불러오는 중...</p>
      </div>
    );
  }

  const saved = autobiography.photo_placements !== null && !dirty;

  return (
    <section className="rounded-2xl border border-black/10 p-6">
      <h3 className="text-lg font-medium text-black">책에 실을 사진 고르기</h3>
      <p className="mt-1 text-sm leading-relaxed text-black/50">
        책에 넣고 싶은 사진과 위치를 골라주세요. 여기서 고른 사진만 책에 들어가요 —
        고르지 않으면 사진 없이 글만 실려요. 넣고 싶은 사진이 목록에 없다면 새로 올릴
        수도 있어요.
      </p>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => void handleUpload(e.target.files)}
      />
      <button
        type="button"
        disabled={uploading}
        onClick={() => fileInputRef.current?.click()}
        className="mt-4 w-full rounded-xl border border-dashed border-black/25 py-3 text-sm text-black/60 transition-colors hover:border-black/50 hover:text-black disabled:opacity-40"
      >
        {uploading ? "올리는 중..." : "+ 책에 넣을 사진 새로 올리기"}
      </button>

      {photos.length === 0 && (
        <p className="mt-4 text-sm text-black/40">
          아직 올린 사진이 없어요. 위 버튼으로 사진을 올리면 여기서 배치를 정할 수 있어요.
        </p>
      )}

      <div className="mt-5 flex flex-col gap-4">
        {photos.map((photo) => {
          const selection = selections.get(photo.id);
          return (
            <div key={photo.id} className="flex gap-4 rounded-xl border border-black/10 p-4">
              {/* eslint-disable-next-line @next/next/no-img-element -- S3 도메인이
              next/image remotePatterns에 등록돼 있지 않아 일반 img로 둔다. */}
              <img
                src={photo.s3_url}
                alt={photo.user_comment ?? ""}
                className="h-24 w-24 shrink-0 rounded-lg object-cover"
              />
              <div className="flex min-w-0 flex-1 flex-col gap-2">
                <label className="flex items-center gap-2 text-base text-black">
                  <input
                    type="checkbox"
                    checked={selection !== undefined}
                    onChange={(e) =>
                      updateSelection(
                        photo.id,
                        e.target.checked
                          ? {
                              media_asset_id: photo.id,
                              chapter_index: chapters[0]?.chapter_index ?? 1,
                              slot: "chapter_top",
                              caption: photo.user_comment,
                            }
                          : null,
                      )
                    }
                    className="h-5 w-5 accent-black"
                  />
                  책에 넣기
                </label>
                {selection && (
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      value={selection.chapter_index}
                      onChange={(e) =>
                        updateSelection(photo.id, {
                          ...selection,
                          chapter_index: Number(e.target.value),
                        })
                      }
                      className="rounded-lg border border-black/15 px-3 py-2 text-sm"
                    >
                      {chapters.map((chapter) => (
                        <option key={chapter.chapter_index} value={chapter.chapter_index}>
                          {chapter.chapter_index}장. {chapter.title ?? ""}
                        </option>
                      ))}
                    </select>
                    <select
                      value={selection.slot}
                      onChange={(e) =>
                        updateSelection(photo.id, {
                          ...selection,
                          slot: e.target.value as PhotoPlacementSlot,
                        })
                      }
                      className="rounded-lg border border-black/15 px-3 py-2 text-sm"
                    >
                      {SLOT_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <input
                      value={selection.caption ?? ""}
                      onChange={(e) =>
                        updateSelection(photo.id, {
                          ...selection,
                          caption: e.target.value || null,
                        })
                      }
                      placeholder="사진 설명 (선택)"
                      className="min-w-40 flex-1 rounded-lg border border-black/15 px-3 py-2 text-sm outline-none placeholder:text-black/35 focus:border-black"
                    />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {error && <p className="mt-4 text-sm text-black/60">{error}</p>}

      <div className="mt-5 flex items-center gap-3">
        <Button variant="secondary" onClick={() => void handleSave()} disabled={saving || !dirty}>
          {saving ? "저장하는 중..." : "사진 배치 저장"}
        </Button>
        {saved && <p className="text-sm text-black/40">저장됐어요. 이제 책으로 만들면 반영돼요.</p>}
      </div>
    </section>
  );
}
