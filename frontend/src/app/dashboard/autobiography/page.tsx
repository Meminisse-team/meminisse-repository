"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import { ApiError } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type { Autobiography, ChapterDraft, TocCandidate } from "@/types/api";

const POLL_INTERVAL_MS = 4000;

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
    if (hasSelectedToc && !bio.final_content) {
      const list = await autobiographiesApi.listChapters(bio.id);
      setChapters(list);
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
    setLoading(true);
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

    if (waitingOnChapters || waitingOnFinalize || waitingOnConsolidate || waitingOnPdf) {
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
      startPolling(() => void load());
    } catch {
      setError("챕터 집필을 시작하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleFinalize() {
    if (!autobiography) return;
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
      <h1 className="mb-8 font-serif-kr text-2xl text-black">나의 자서전</h1>

      {error && <p className="mb-6 text-base text-black/60">{error}</p>}

      {autobiography.final_content ? (
        <FinalManuscript
          title={autobiography.title}
          content={autobiography.final_content}
          pdfUrl={autobiography.pdf_url}
          busy={busy}
          pdfTriggered={pdfTriggered}
          onGeneratePdf={handleGeneratePdf}
        />
      ) : selectedIndex !== null ? (
        <ChapterProgress
          chapters={chapters}
          allWritten={chaptersAllWritten}
          busy={busy}
          finalizeTriggered={finalizeTriggered}
          onWriteAll={handleWriteAll}
          onFinalize={handleFinalize}
        />
      ) : candidates.length > 0 ? (
        <TocSelection candidates={candidates} selecting={selecting} onSelect={handleSelectToc} />
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
        <p className="text-sm text-black/40">이야기를 정리하고 있어요. 이 화면을 열어두면 자동으로 넘어가요...</p>
      ) : (
        <Button onClick={onConsolidate} disabled={busy}>
          {busy ? "시작하는 중..." : "이야기 정리하기"}
        </Button>
      )}
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
      {candidates.map((candidate, index) => (
        <div key={index} className="rounded-2xl border border-black/10 p-6">
          <p className="mb-4 text-sm text-black/40">목차 {index + 1}</p>
          <ol className="flex flex-col gap-2">
            {candidate.chapters.map((chapter) => (
              <li key={chapter.chapter_index} className="text-base text-black">
                {chapter.chapter_index}장. {chapter.title}
              </li>
            ))}
          </ol>
          <Button
            variant="secondary"
            className="mt-5 w-full"
            disabled={selecting !== null}
            onClick={() => onSelect(index)}
          >
            {selecting === index ? "선택하는 중..." : "이 목차로 진행"}
          </Button>
        </div>
      ))}
    </div>
  );
}

function ChapterProgress({
  chapters,
  allWritten,
  busy,
  finalizeTriggered,
  onWriteAll,
  onFinalize,
}: {
  chapters: ChapterDraft[];
  allWritten: boolean;
  busy: boolean;
  finalizeTriggered: boolean;
  onWriteAll: () => void;
  onFinalize: () => void;
}) {
  const STATUS_LABEL: Record<ChapterDraft["status"], string> = {
    draft: "집필 전",
    reviewed: "집필 완료",
    finalized: "최종 확정",
  };

  return (
    <div className="flex flex-col gap-6">
      <ol className="flex flex-col gap-3">
        {chapters.map((chapter) => (
          <li
            key={chapter.id}
            className="flex items-center justify-between gap-4 rounded-2xl border border-black/10 p-5"
          >
            <span className="min-w-0 flex-1 text-base text-black">
              {chapter.chapter_index}장. {chapter.title ?? "제목 준비 중"}
            </span>
            <span className="shrink-0 text-sm text-black/40">{STATUS_LABEL[chapter.status]}</span>
          </li>
        ))}
      </ol>

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
        <Button onClick={onFinalize} disabled={busy}>
          {busy ? "최종본을 만드는 중..." : "최종본 만들기"}
        </Button>
      )}
      {allWritten && finalizeTriggered && (
        <p className="text-sm text-black/40">최종 원고를 다듬고 있어요. 잠시만 기다려주세요...</p>
      )}
    </div>
  );
}

function FinalManuscript({
  title,
  content,
  pdfUrl,
  busy,
  pdfTriggered,
  onGeneratePdf,
}: {
  title: string | null;
  content: string;
  pdfUrl: string | null;
  busy: boolean;
  pdfTriggered: boolean;
  onGeneratePdf: () => void;
}) {
  return (
    <article className="flex flex-col gap-6">
      <h2 className="font-serif-kr text-xl text-black">{title ?? "제목 없음"}</h2>

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

      <p className="whitespace-pre-wrap text-base leading-loose text-black/80">{content}</p>
    </article>
  );
}
