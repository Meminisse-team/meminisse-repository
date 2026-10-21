"use client";

import Link from "next/link";
import { Button } from "@/components/ui/Button";

export function NoTocYet({ busy, onGenerate }: { busy: boolean; onGenerate: () => void }) {
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
