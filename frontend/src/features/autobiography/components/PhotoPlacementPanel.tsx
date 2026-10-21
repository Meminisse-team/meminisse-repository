"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import { mediaApi } from "@/lib/api/media";
import type { Autobiography, ChapterDraft, MediaAsset, PhotoPlacement, PhotoPlacementSlot } from "@/types/api";
import { SLOT_OPTIONS } from "./SLOT_OPTIONS";

export function PhotoPlacementPanel({
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
