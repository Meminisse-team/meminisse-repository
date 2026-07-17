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
  const [chatMode, setChatMode] = useState<"queue" | "episode">("queue");
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

  function handleAddEpisode() {
    // "권장"이지 "차단"은 아니다 — 준비된 질문과 내용이 겹칠 수 있다는 것만 알리고,
    // 계속할지는 사용자가 정한다(2026-07-16 요청).
    const confirmed = window.confirm(
      "이미 준비된 질문과 겹칠 수 있어요. 질문에 먼저 모두 답한 뒤 추가하시는 걸 권장해요. 그래도 새 에피소드를 시작할까요?"
    );
    if (!confirmed) return;
    setChatMode("episode");
    setChatOpen(true);
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
        onClick={() => {
          setChatMode("queue");
          setChatOpen(true);
        }}
        className="w-full max-w-md rounded-3xl border border-black/10 p-6 text-left transition-colors hover:border-black/30"
      >
        <p className="text-sm text-black/40">오늘의 대화</p>
        <p className="mt-2 text-lg leading-relaxed text-black">{preview}</p>
        <p className="mt-4 text-sm text-black/40">눌러서 이어가기 →</p>
      </button>

      <button
        type="button"
        onClick={handleAddEpisode}
        className="w-full max-w-md rounded-3xl border border-black/10 p-6 text-left transition-colors hover:border-black/30"
      >
        <p className="text-sm text-black/40">에피소드 추가</p>
        <p className="mt-2 text-lg leading-relaxed text-black">질문에 없는 나만의 이야기를 자유롭게 들려주세요</p>
        <p className="mt-4 text-sm text-black/40">눌러서 시작하기 →</p>
      </button>

      <Link
        href="/dashboard/autobiography"
        className="w-full max-w-md rounded-3xl border border-black/10 p-6 text-left transition-colors hover:border-black/30"
      >
        <p className="text-sm text-black/40">자서전 집필</p>
        <p className="mt-2 text-lg leading-relaxed text-black">지금 쓰고 있는 자서전을 이어서 써보세요</p>
        <p className="mt-4 text-sm text-black/40">눌러서 이어가기 →</p>
      </Link>

      <Link
        href="/dashboard/bookshelf"
        className="w-full max-w-md rounded-3xl border border-black/10 p-6 text-left transition-colors hover:border-black/30"
      >
        <p className="text-sm text-black/40">나의 책장</p>
        <p className="mt-2 text-lg leading-relaxed text-black">완성한 자서전들을 모아 볼 수 있어요</p>
        <p className="mt-4 text-sm text-black/40">눌러서 확인하기 →</p>
      </Link>

      <ChatOverlay
        open={chatOpen}
        onClose={handleChatClose}
        resumeSessionId={resumeSessionId}
        onSessionChanged={() => void refreshPreview()}
        startMode={chatMode}
      />
    </main>
  );
}
