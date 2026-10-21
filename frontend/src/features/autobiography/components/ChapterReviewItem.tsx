"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import type { ChapterDraft } from "@/types/api";
import { chapterFlagCount } from "../utils/chapterFlags";
import { Spinner } from "./Spinner";
import { STATUS_LABEL } from "../utils/chapterFlags";

export function ChapterReviewItem({
  autobiographyId,
  chapter,
  writing,
  rewriting,
  acknowledged,
  onAcknowledge,
  onSaved,
  onRewrite,
}: {
  autobiographyId: string;
  chapter: ChapterDraft;
  writing: boolean;
  rewriting: boolean;
  acknowledged: boolean;
  onAcknowledge: () => void;
  onSaved: (content: string) => void;
  onRewrite: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(chapter.content ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const flagCount = chapterFlagCount(chapter);
  const showFlagBadge = flagCount > 0 && !acknowledged;
  const canExpand = chapter.content !== null;

  function startEditing() {
    setDraft(chapter.content ?? "");
    setSaveError(null);
    setEditing(true);
  }

  async function handleSave() {
    const trimmed = draft.trim();
    if (!trimmed) {
      setSaveError("본문을 비워둘 수는 없어요.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await autobiographiesApi.updateChapterContent(autobiographyId, chapter.id, trimmed);
      onSaved(trimmed);
      setEditing(false);
    } catch {
      setSaveError("수정한 내용을 저장하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSaving(false);
    }
  }

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
          {editing ? (
            <div className="flex flex-col gap-3">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={saving}
                rows={12}
                className="w-full rounded-xl border border-black/15 p-4 text-base leading-relaxed text-black outline-none focus:border-black disabled:opacity-60"
              />
              {saveError && <p className="text-sm text-black/60">{saveError}</p>}
              <div className="flex items-center gap-3">
                <Button onClick={() => void handleSave()} disabled={saving}>
                  {saving ? "저장하는 중..." : "저장"}
                </Button>
                <button
                  type="button"
                  onClick={() => setEditing(false)}
                  disabled={saving}
                  className="text-sm text-black/50 underline underline-offset-4 hover:text-black/70 disabled:opacity-40"
                >
                  취소
                </button>
              </div>
            </div>
          ) : (
            <p className="whitespace-pre-wrap text-base leading-relaxed text-black/80">
              {chapter.content}
            </p>
          )}
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
          {!editing && (
            <div className="flex items-center gap-4">
              <button
                type="button"
                onClick={startEditing}
                disabled={rewriting}
                className="text-sm text-black/50 underline underline-offset-4 hover:text-black/70 disabled:no-underline disabled:opacity-40"
              >
                직접 수정
              </button>
              <button
                type="button"
                onClick={onRewrite}
                disabled={rewriting}
                className="text-sm text-black/50 underline underline-offset-4 hover:text-black/70 disabled:no-underline disabled:opacity-40"
              >
                {rewriting ? "다시 쓰고 있어요..." : "이 챕터 다시 쓰기"}
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
