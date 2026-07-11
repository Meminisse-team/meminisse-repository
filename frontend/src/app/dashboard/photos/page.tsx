"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { mediaApi } from "@/lib/api/media";
import type { MediaAsset } from "@/types/api";

const MAX_PHOTOS = 20;

export default function PhotosPage() {
  const [photos, setPhotos] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const reachedLimit = photos.length >= MAX_PHOTOS;

  useEffect(() => {
    mediaApi
      .list()
      .then(setPhotos)
      .catch(() => setError("사진을 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, []);

  async function handleFileSelected(files: FileList | null) {
    if (!files || files.length === 0 || reachedLimit) return;
    setUploading(true);
    setError(null);
    try {
      const uploaded = await mediaApi.upload({ file: files[0] });
      setPhotos((prev) => [uploaded, ...prev]);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? "사진을 올리지 못했어요. 잠시 후 다시 시도해주세요."
          : "알 수 없는 오류가 발생했어요.",
      );
    } finally {
      setUploading(false);
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
          // eslint-disable-next-line @next/next/no-img-element -- S3 원본 도메인이 next/image
          // remotePatterns에 아직 등록돼 있지 않아, 스캐폴딩 단계에서는 일반 img로 둔다.
          <div key={photo.id} className="flex flex-col gap-2">
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
        className="hidden"
        onChange={(e) => void handleFileSelected(e.target.files)}
      />
      <button
        type="button"
        disabled={reachedLimit || uploading}
        onClick={() => fileInputRef.current?.click()}
        className="mt-8 w-full rounded-2xl border border-dashed border-black/25 py-5 text-base text-black/60 transition-colors hover:border-black/50 hover:text-black disabled:cursor-not-allowed disabled:opacity-40"
      >
        {reachedLimit
          ? "최대 20장까지 등록할 수 있어요"
          : uploading
            ? "올리는 중..."
            : "+ 사진 추가 등록"}
      </button>
    </main>
  );
}
