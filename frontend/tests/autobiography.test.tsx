import { it, expect, vi, afterEach } from "vitest";
import { act, render, renderHook, screen, fireEvent } from "@testing-library/react";
import { jobReducer } from "@/features/autobiography/hooks/useAutobiographyJob";
import { hasStatusChanged, useAutobiographyPolling } from "@/features/autobiography/hooks/useAutobiographyPolling";
import { autobiographiesApi } from "@/features/autobiography/api/autobiographies";
import { TocSelection } from "@/features/autobiography/components/TocSelection";
import { ProgressGate } from "@/features/autobiography/components/ProgressGate";
import type { Autobiography, AutobiographyPollingStatus } from "@/types/api";
vi.mock("@/features/autobiography/api/autobiographies", () => ({ autobiographiesApi: { getPollingStatus: vi.fn() } }));
const bio: Autobiography = { id: "book", user_id: "user", title: null, status: "in_progress", toc_data: null, style_bible: null, book_synopsis: null, final_content: null, pdf_url: null, pdf_version: null, photo_placements: null, created_at: "now", updated_at: "now", completed_session_count: 4, progress: { minimum_sessions: 6, recommended_sessions: 8, progress_total: 10 } };
const status: AutobiographyPollingStatus = { id: "book", status: "in_progress", final_content_ready: false, pdf_url: null, pdf_version: null, chapters: [] };
afterEach(() => vi.useRealTimers());
it("enforces mutually exclusive work and ignores stale completion", () => {
  expect(jobReducer("idle", { type: "start", job: "finalizing" })).toBe("finalizing");
  expect(jobReducer("finalizing", { type: "start", job: "generating_pdf" })).toBe("finalizing");
  expect(jobReducer("generating_pdf", { type: "finish", job: "writing" })).toBe("generating_pdf");
  expect(jobReducer("generating_pdf", { type: "finish", job: "generating_pdf" })).toBe("idle");
});
it("does not treat rotating signed URLs as manuscript changes", () => {
  expect(hasStatusChanged({ ...bio, pdf_url: "old-url", pdf_version: "v1" }, [], { ...status, pdf_url: "new-url", pdf_version: "v1" })).toBe(false);
  expect(hasStatusChanged({ ...bio, pdf_version: "v1" }, [], { ...status, pdf_version: "v2" })).toBe(true);
});
it("polls status only, reloads on change and cancels timers on unmount", async () => {
  vi.useFakeTimers();
  vi.mocked(autobiographiesApi.getPollingStatus).mockResolvedValue(status);
  const reload = vi.fn(async () => {});
  const error = vi.fn();
  const { unmount } = renderHook(() => useAutobiographyPolling({ autobiography: bio, chapters: [], enabled: true, onChanged: reload, onError: error }));
  await act(() => vi.advanceTimersByTimeAsync(4000));
  expect(reload).not.toHaveBeenCalled();
  vi.mocked(autobiographiesApi.getPollingStatus).mockResolvedValue({ ...status, final_content_ready: true });
  await act(() => vi.advanceTimersByTimeAsync(4000));
  expect(reload).toHaveBeenCalledTimes(1);
  unmount(); const calls = vi.mocked(autobiographiesApi.getPollingStatus).mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(8000));
  expect(autobiographiesApi.getPollingStatus).toHaveBeenCalledTimes(calls);
});
it("uses server thresholds instead of hardcoded progress numbers", () => {
  render(<ProgressGate completedCount={4} policy={bio.progress} />);
  expect(screen.getByText(/시작 가능 6/)).toHaveTextContent("권장 8");
});
it("selects a TOC candidate and locks selection while pending", () => {
  const onSelect = vi.fn();
  const { rerender } = render(<TocSelection candidates={[{ chapters: [{ chapter_index: 1, title: "어린 시절", theme_keywords: [] }] }]} selecting={null} onSelect={onSelect} />);
  const button = screen.getByRole("button"); fireEvent.click(button);
  expect(onSelect).toHaveBeenCalledWith(0);
  rerender(<TocSelection candidates={[{ chapters: [] }]} selecting={0} onSelect={onSelect} />);
  expect(screen.getByRole("button")).toBeDisabled();
});

it("announces review flags without hiding them merely because writing completed", async () => {
  const { ChapterReviewItem } = await import("@/features/autobiography/components/ChapterReviewItem");
  const chapter = { id: "chapter", autobiography_id: "book", chapter_index: 1, title: "기억", chapter_synopsis: null, content: "원고", source_event_ids: [], factcheck_report: { checked_at: "now", total_facts: 1, unchecked_facts: 0, flags: [{ fact_type: "date", raw_text: "1900", reason: "needs review" }] }, groundedness_report: null, status: "draft" as const, created_at: "now", updated_at: "now" };
  render(<ChapterReviewItem autobiographyId="book" chapter={chapter} writing={false} rewriting={false} acknowledged={false} onAcknowledge={vi.fn()} onSaved={vi.fn()} onRewrite={vi.fn()} />);
  expect(screen.getByText("확인 필요 1곳")).toBeInTheDocument();
});

it("shows PDF progress and prevents duplicate generation", async () => {
  const { FinalManuscript } = await import("@/features/autobiography/components/FinalManuscript");
  render(<FinalManuscript autobiography={{ ...bio, final_content: "최종 원고" }} chapters={[]} busy={true} pdfTriggered={true} rewritingChapterIds={new Set()} onGeneratePdf={vi.fn()} onAutobiographyChange={vi.fn()} onChapterContentSaved={vi.fn()} onRewrite={vi.fn()} onRegenerateToc={vi.fn()} />);
  expect(screen.getByRole("button", { name: "책으로 만드는 중..." })).toBeDisabled();
});

it("never overlaps a pending status request or reloads after unmount", async () => {
  vi.useFakeTimers();
  let resolve!: (result: AutobiographyPollingStatus) => void;
  vi.mocked(autobiographiesApi.getPollingStatus).mockReset().mockReturnValue(new Promise(r => { resolve = r; }));
  const reload = vi.fn(async () => {});
  const { unmount } = renderHook(() => useAutobiographyPolling({ autobiography: bio, chapters: [], enabled: true, onChanged: reload, onError: vi.fn() }));
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(autobiographiesApi.getPollingStatus).toHaveBeenCalledTimes(1);
  unmount();
  await act(async () => resolve({ ...status, final_content_ready: true }));
  expect(reload).not.toHaveBeenCalled();
});
