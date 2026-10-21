"use client";

import type { PhotoPlacementSlot } from "@/types/api";

export const SLOT_OPTIONS: { value: PhotoPlacementSlot; label: string }[] = [
  { value: "chapter_top", label: "챕터 첫머리" },
  { value: "full_page_before", label: "챕터 앞 전면 페이지" },
];
