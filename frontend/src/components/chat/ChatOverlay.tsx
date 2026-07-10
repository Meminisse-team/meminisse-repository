"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  assistantOpeningLine,
  dummyChatHistory,
  type DummyChatMessage,
} from "@/lib/dummy/chat";

interface ChatOverlayProps {
  open: boolean;
  onClose: () => void;
  /** 이전 대화 기록(더미)이 있으면 이어서 보여준다 — 없으면 첫 인사만 보여준다.
   * 실제 세션 이어보기는 백엔드에 세션 목록 조회 API가 생기면 교체할 부분. */
  hasPreviousSession?: boolean;
}

/**
 * '오늘의 대화' 클릭 시 뜨는 채팅 컴포넌트. 지금은 발화 내역이 전부 더미다 —
 * 실제 전송은 lib/api/interviews.ts(interviewsApi.create/sendMessage)로 이미
 * 연동 가능하니, sendDummyReply 자리를 그 호출로 바꾸기만 하면 실제 파이프라인에
 * 그대로 연결된다.
 */
export function ChatOverlay({ open, onClose, hasPreviousSession = true }: ChatOverlayProps) {
  const [messages, setMessages] = useState<DummyChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setMessages(
      hasPreviousSession
        ? dummyChatHistory
        : [
            {
              id: "opening",
              role: "assistant",
              content: assistantOpeningLine,
              createdAt: new Date().toISOString(),
            },
          ],
    );
  }, [open, hasPreviousSession]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  if (!open) return null;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!content || sending) return;

    const userMessage: DummyChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content,
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setSending(true);

    // TODO: interviewsApi.sendMessage(sessionId, content)로 교체.
    window.setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        {
          id: `local-reply-${Date.now()}`,
          role: "assistant",
          content: "말씀해주셔서 감사해요. 그때 기분은 어떠셨나요?",
          createdAt: new Date().toISOString(),
        },
      ]);
      setSending(false);
    }, 900);
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-white animate-fade-up">
      <header className="flex items-center justify-between border-b border-black/10 px-6 py-4">
        <span className="font-serif-kr text-lg text-black">오늘의 대화</span>
        <button
          type="button"
          onClick={onClose}
          className="text-base text-black/60 underline-offset-4 hover:text-black hover:underline"
        >
          대화 종료
        </button>
      </header>

      <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <p
              className={`max-w-[75%] rounded-2xl px-4 py-3 text-base leading-relaxed ${
                m.role === "user" ? "bg-black text-white" : "bg-black/5 text-black"
              }`}
            >
              {m.content}
            </p>
          </div>
        ))}
        {sending && <p className="text-sm text-black/40">Meminisse가 듣고 있어요...</p>}
      </div>

      <form onSubmit={handleSubmit} className="flex items-center gap-3 border-t border-black/10 px-6 py-4">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="편하게 말씀해주세요"
          className="flex-1 rounded-full border border-black/15 px-5 py-3 text-base outline-none placeholder:text-black/35 focus:border-black"
        />
        <button
          type="submit"
          disabled={!input.trim() || sending}
          className="shrink-0 whitespace-nowrap rounded-full bg-black px-5 py-3 text-base text-white disabled:opacity-40"
        >
          보내기
        </button>
      </form>
    </div>
  );
}
