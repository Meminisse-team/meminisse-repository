"use client";

import { useState } from "react";
import Link from "next/link";

import { ChatOverlay } from "@/components/chat/ChatOverlay";
import { RippleLogo } from "@/components/ui/RippleLogo";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import { dummyLastSessionPreview } from "@/lib/dummy/chat";

export default function DashboardHomePage() {
  const { user } = useCurrentUser();
  const [chatOpen, setChatOpen] = useState(false);

  return (
    <main className="flex flex-col items-center gap-10 px-6 pb-16 pt-20">
      <Link href="/dashboard" className="flex flex-col items-center gap-6">
        <RippleLogo />
        <div className="text-center">
          <p className="font-serif-kr text-2xl text-black">Meminisse</p>
          {user && <p className="mt-1 text-base text-black/50">{user.name}님, 오늘도 반가워요</p>}
        </div>
      </Link>

      <button
        type="button"
        onClick={() => setChatOpen(true)}
        className="w-full max-w-md rounded-3xl border border-black/10 p-6 text-left transition-colors hover:border-black/30"
      >
        <p className="text-sm text-black/40">오늘의 대화</p>
        <p className="mt-2 text-lg leading-relaxed text-black">{dummyLastSessionPreview}</p>
        <p className="mt-4 text-sm text-black/40">눌러서 이어가기 →</p>
      </button>

      <ChatOverlay open={chatOpen} onClose={() => setChatOpen(false)} />
    </main>
  );
}
