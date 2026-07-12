"use client";

import { useEffect, useRef, useState } from "react";

import { RippleRings } from "@/components/ui/RippleRings";
import { ApiError } from "@/lib/api/client";
import { mediaApi } from "@/lib/api/media";
import type { MediaAsset } from "@/types/api";

const MAX_PHOTOS = 20;

export default function PhotosPage() {
  const [photos, setPhotos] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const reachedLimit = photos.length >= MAX_PHOTOS;
  const uploading = uploadProgress !== null;

  useEffect(() => {
    mediaApi
      .list()
      .then(setPhotos)
      .catch(() => setError("사진을 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, []);

  async function handleFileSelected(files: FileList | null) {
    if (!files || files.length === 0 || reachedLimit) return;
    // 한 번에 여러 장을 골라도 한 장씩만 올라가던 문제 수정 — 남은 등록 가능 수만큼만
    // 잘라서, 순서대로(동시에 X) 업로드한다. 진행 상황(done/total)을 화면에 보여준다.
    const remaining = MAX_PHOTOS - photos.length;
    const selected = Array.from(files).slice(0, remaining);
    setError(null);
    setUploadProgress({ done: 0, total: selected.length });
    try {
      for (const file of selected) {
        const uploaded = await mediaApi.upload({ file });
        setPhotos((prev) => [uploaded, ...prev]);
        setUploadProgress((prev) => (prev ? { done: prev.done + 1, total: prev.total } : prev));
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? "사진을 올리지 못했어요. 잠시 후 다시 시도해주세요."
          : "알 수 없는 오류가 발생했어요.",
      );
    } finally {
      setUploadProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <main className="px-6 pb-10 pt-14">
      <div className="mb-8 flex items-end justify-between">
        <h1 className="font-serif-kr text-2xl text-black">사진첩</h1>
        <span className="text-sm text-black/40">
          {photos.length} / {MAX_PHOTOS}
        </span>
      </div>

      {loading && <p className="text-black/50">불러오는 중...</p>}
      {error && <p className="mb-4 text-sm text-black/50">{error}</p>}
      {!loading && photos.length === 0 && !error && (
        <p className="mb-8 text-black/50">아직 등록된 사진이 없어요.</p>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {photos.map((photo) => (
          <div key={photo.id} className="flex flex-col gap-2">
            {/* eslint-disable-next-line @next/next/no-img-element -- S3 원본 도메인이 next/image
            remotePatterns에 아직 등록돼 있지 않아, 스캐폴딩 단계에서는 일반 img로 둔다. */}
            <img
              src={photo.s3_url}
              alt={photo.user_comment ?? "등록된 사진"}
              className="aspect-square w-full rounded-2xl bg-black/5 object-cover"
            />
            <p className="truncate text-sm text-black/60">
              {photo.user_comment || photo.location_at_time || "사진"}
            </p>
          </div>
        ))}
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => void handleFileSelected(e.target.files)}
      />
      <button
        type="button"
        disabled={reachedLimit || uploading}
        onClick={() => fileInputRef.current?.click()}
        className="relative mt-8 w-full rounded-2xl border border-dashed border-black/25 py-5 text-base text-black/60 transition-colors hover:border-black/50 hover:text-black disabled:cursor-not-allowed disabled:opacity-40"
      >
        <span aria-hidden className="pointer-events-none absolute inset-0">
          <RippleRings className="text-black/15" />
        </span>
        <span className="relative z-10">
          {reachedLimit
            ? "최대 20장까지 등록할 수 있어요"
            : uploadProgress
              ? `올리는 중... (${uploadProgress.done}/${uploadProgress.total})`
              : "+ 사진 추가 등록 (여러 장 선택 가능)"}
        </span>
      </button>
    </main>
  );
}
