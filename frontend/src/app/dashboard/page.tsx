"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { ChatOverlay } from "@/components/chat/ChatOverlay";
import { RippleLogo } from "@/components/ui/RippleLogo";
import { interviewsApi } from "@/lib/api/interviews";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type { InterviewSession } from "@/types/api";

const DEFAULT_PREVIEW = "아직 나눈 이야기가 없어요. 눌러서 첫 대화를 시작해보세요.";

export default function DashboardHomePage() {
  const { user } = useCurrentUser();
  const [chatOpen, setChatOpen] = useState(false);
  const [latestSession, setLatestSession] = useState<InterviewSession | null>(null);
  const [preview, setPreview] = useState(DEFAULT_PREVIEW);

  const refreshPreview = useCallback(async () => {
    try {
      const sessions = await interviewsApi.list();
      if (sessions.length === 0) {
        setLatestSession(null);
        setPreview(DEFAULT_PREVIEW);
        return;
      }
      const latest = sessions[0];
      setLatestSession(latest);
      const detail = await interviewsApi.get(latest.id);
      const lastMessage = detail.chat_logs.at(-1);
      setPreview(lastMessage ? lastMessage.content : DEFAULT_PREVIEW);
    } catch {
      setLatestSession(null);
      setPreview(DEFAULT_PREVIEW);
    }
  }, []);

  useEffect(() => {
    void refreshPreview();
  }, [refreshPreview]);

  function handleChatClose() {
    setChatOpen(false);
    void refreshPreview();
  }

  // 열려 있는(미완료) 세션만 이어간다 — 이미 완료된 세션에 다시 발화를 얹으면
  // Phase 2 후처리 파이프라인이 잘못 재실행될 수 있어, 완료된 세션은 미리보기로만
  // 보여주고 새로 시작할 때는 첫 발화 시점에 새 세션을 만든다(ChatOverlay 참조).
  const resumeSessionId = latestSession?.status === "open" ? latestSession.id : null;

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
        <p className="mt-2 text-lg leading-relaxed text-black">{preview}</p>
        <p className="mt-4 text-sm text-black/40">눌러서 이어가기 →</p>
      </button>

      <ChatOverlay
        open={chatOpen}
        onClose={handleChatClose}
        resumeSessionId={resumeSessionId}
        onSessionChanged={() => void refreshPreview()}
      />
    </main>
  );
}
