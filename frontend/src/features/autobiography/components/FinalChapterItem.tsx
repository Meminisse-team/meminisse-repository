"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import type { ChapterDraft } from "@/types/api";
import { Spinner } from "./Spinner";

export function FinalChapterItem({
  autobiographyId,
  chapter,
  rewriting,
  onSaved,
  onRewrite,
}: {
  autobiographyId: string;
  chapter: ChapterDraft;
  rewriting: boolean;
  onSaved: (content: string) => void;
  onRewrite: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(chapter.content ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEditing() {
    setDraft(chapter.content ?? "");
    setError(null);
    setEditing(true);
  }

  async function handleSave() {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError("본문을 비워둘 수는 없어요.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await autobiographiesApi.updateChapterContent(autobiographyId, chapter.id, trimmed);
      onSaved(trimmed);
      setEditing(false);
    } catch {
      setError("수정한 내용을 저장하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="rounded-2xl border border-black/10 p-6">
      <h3 className="font-serif-kr text-lg text-black">
        {chapter.chapter_index}장. {chapter.title ?? ""}
      </h3>

      {editing ? (
        <div className="mt-4 flex flex-col gap-3">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={saving}
            rows={12}
            className="w-full rounded-xl border border-black/15 p-4 text-base leading-relaxed text-black outline-none focus:border-black disabled:opacity-60"
          />
          {error && <p className="text-sm text-black/60">{error}</p>}
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
        <>
          <p className="mt-4 whitespace-pre-wrap text-base leading-loose text-black/80">
            {rewriting ? (
              <span className="flex items-center gap-2 text-black/40">
                <Spinner />
                <span className="animate-pulse">AI가 다시 쓰고 있어요...</span>
              </span>
            ) : (
              chapter.content
            )}
          </p>
          {!rewriting && (
            <div className="mt-4 flex items-center gap-4">
              <button
                type="button"
                onClick={startEditing}
                className="text-sm text-black/50 underline underline-offset-4 hover:text-black/70"
              >
                직접 수정
              </button>
              <button
                type="button"
                onClick={onRewrite}
                className="text-sm text-black/50 underline underline-offset-4 hover:text-black/70"
              >
                AI로 다시 쓰기
              </button>
            </div>
          )}
        </>
      )}
    </li>
  );
}
