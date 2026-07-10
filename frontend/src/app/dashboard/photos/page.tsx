"use client";

import { useRef, useState } from "react";

import { MAX_PHOTOS, dummyPhotos, nextGradient, type DummyPhoto } from "@/lib/dummy/photos";

/**
 * 사진첩 탭. 백엔드에 "내 미디어 목록 조회" API가 아직 없어(POST 업로드만 존재)
 * 지금은 더미 배열에 로컬로만 추가한다 — 목록 조회 API가 생기면 이 state를
 * mediaApi 호출 결과로 교체하면 된다.
 */
export default function PhotosPage() {
  const [photos, setPhotos] = useState<DummyPhoto[]>(dummyPhotos);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const reachedLimit = photos.length >= MAX_PHOTOS;

  function handleFileSelected(files: FileList | null) {
    if (!files || files.length === 0 || reachedLimit) return;
    const file = files[0];
    const [colorFrom, colorTo] = nextGradient(photos.length);
    setPhotos((prev) => [
      ...prev,
      {
        id: `local-${Date.now()}`,
        caption: file.name,
        takenYear: null,
        colorFrom,
        colorTo,
      },
    ]);
  }

  return (
    <main className="px-6 pb-10 pt-14">
      <div className="mb-8 flex items-end justify-between">
        <h1 className="font-serif-kr text-2xl text-black">사진첩</h1>
        <span className="text-sm text-black/40">
          {photos.length} / {MAX_PHOTOS}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {photos.map((photo) => (
          <div key={photo.id} className="flex flex-col gap-2">
            <div
              className="aspect-square rounded-2xl"
              style={{
                background: `linear-gradient(135deg, ${photo.colorFrom}, ${photo.colorTo})`,
              }}
            />
            <p className="truncate text-sm text-black/60">{photo.caption}</p>
          </div>
        ))}
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => handleFileSelected(e.target.files)}
      />
      <button
        type="button"
        disabled={reachedLimit}
        onClick={() => fileInputRef.current?.click()}
        className="mt-8 w-full rounded-2xl border border-dashed border-black/25 py-5 text-base text-black/60 transition-colors hover:border-black/50 hover:text-black disabled:cursor-not-allowed disabled:opacity-40"
      >
        {reachedLimit ? "최대 20장까지 등록할 수 있어요" : "+ 사진 추가 등록"}
      </button>
    </main>
  );
}
