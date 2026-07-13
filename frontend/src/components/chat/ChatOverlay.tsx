"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { interviewsApi } from "@/lib/api/interviews";
import { mediaApi } from "@/lib/api/media";
import { RippleRings } from "@/components/ui/RippleRings";
import { stripMarkdown } from "@/lib/format/stripMarkdown";
import type { ChatMessage, MediaAsset } from "@/types/api";

interface ChatOverlayProps {
  open: boolean;
  onClose: () => void;
  /** 이어갈 열린(open) 세션이 있으면 그 id, 없으면 null(첫 발화 시점에 새로 만든다). */
  resumeSessionId: string | null;
  /** 세션이 새로 생성되거나 종료돼 부모(대시보드 미리보기)가 갱신해야 할 때 호출. */
  onSessionChanged?: (sessionId: string) => void;
}

const OPENING_LINE = "오늘은 어떤 기억을 함께 떠올려볼까요? 편하게 말씀해주세요.";

/**
 * '오늘의 대화' 클릭 시 뜨는 채팅 컴포넌트. lib/api/interviews.ts를 통해 실제
 * 백엔드(Solar 기반 인터뷰 에이전트)와 대화한다 — 더미 데이터는 쓰지 않는다.
 * resumeSessionId가 있으면 그 세션의 chat_logs를 불러와 이어서 보여주고, 없으면
 * 첫 메시지를 보낼 때 비로소 세션을 만든다(빈 세션이 계속 쌓이는 것을 방지).
 */
export function ChatOverlay({ open, onClose, resumeSessionId, onSessionChanged }: ChatOverlayProps) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [linkedMediaAssetId, setLinkedMediaAssetId] = useState<string | null>(null);
  const [linkedPhoto, setLinkedPhoto] = useState<MediaAsset | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSessionId(resumeSessionId);
    setLinkedMediaAssetId(null);
    setLinkedPhoto(null);
    if (!resumeSessionId) {
      setMessages([]);
      return;
    }
    setLoading(true);
    interviewsApi
      .get(resumeSessionId)
      .then((detail) => {
        setMessages(detail.chat_logs);
        setLinkedMediaAssetId(detail.session_type === "photo" ? detail.linked_media_asset_id : null);
      })
      .catch(() => setError("이전 대화를 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, [open, resumeSessionId]);

  useEffect(() => {
    if (!linkedMediaAssetId) return;
    let cancelled = false;
    mediaApi
      .get(linkedMediaAssetId)
      .then((asset) => {
        if (!cancelled) setLinkedPhoto(asset);
      })
      .catch(() => {
        if (!cancelled) setLinkedPhoto(null);
      });
    return () => {
      cancelled = true;
    };
  }, [linkedMediaAssetId]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  if (!open) return null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
    setInput("");
    setSending(true);
    setError(null);
    try {
      let activeSessionId = sessionId;
      if (!activeSessionId) {
        // 서버가 상황에 따라 session_type을 PHOTO로 자동 전환할 수 있다(고정 질문 큐와
        // 사진 큐를 합쳐 다음 항목을 고르는 백엔드 오케스트레이션, docs/QUESTION_BANK_
        // GUIDE.md 5절 참조) — 요청 바디는 그대로 fixed_question이어도 무방하다.
        const created = await interviewsApi.create({ session_type: "fixed_question" });
        activeSessionId = created.id;
        setSessionId(created.id);
        setLinkedMediaAssetId(created.session_type === "photo" ? created.linked_media_asset_id : null);
        onSessionChanged?.(created.id);
      }
      const turn = await interviewsApi.sendMessage(activeSessionId, content);
      setMessages((prev) => [...prev, turn.user_message, turn.assistant_message]);
    } catch {
      setError("메시지를 보내지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSending(false);
    }
  }

  async function handleEnd() {
    if (sessionId) {
      try {
        await interviewsApi.complete(sessionId);
        onSessionChanged?.(sessionId);
      } catch {
        // 종료 호출이 실패해도 화면은 닫는다 — 다음에 다시 열면 미완료 세션으로 이어진다.
      }
    }
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-white animate-fade-up">
      <header className="flex items-center justify-between border-b border-black/10 px-6 py-4">
        <span className="font-serif-kr text-lg text-black">오늘의 대화</span>
        <button
          type="button"
          onClick={() => void handleEnd()}
          className="text-base text-black/60 underline-offset-4 hover:text-black hover:underline"
        >
          대화 종료
        </button>
      </header>

      <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {linkedPhoto && (
          <div className="flex justify-center pb-2">
            {/* eslint-disable-next-line @next/next/no-img-element -- S3 원본 도메인이 next/image
            remotePatterns에 아직 등록돼 있지 않아, photos/page.tsx와 동일하게 일반 img로 둔다. */}
            <img
              src={linkedPhoto.s3_url}
              alt={linkedPhoto.user_comment ?? "이번 대화의 사진"}
              className="max-h-80 w-auto max-w-full rounded-2xl object-contain shadow-sm"
            />
          </div>
        )}
        {loading && <p className="text-sm text-black/40">불러오는 중...</p>}
        {!loading && messages.length === 0 && (
          <div className="flex justify-start">
            <p className="max-w-[75%] rounded-2xl bg-black/5 px-4 py-3 text-base leading-relaxed text-black">
              {OPENING_LINE}
            </p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <p
              className={`max-w-[75%] rounded-2xl px-4 py-3 text-base leading-relaxed ${
                m.role === "user" ? "bg-black text-white" : "bg-black/5 text-black"
              }`}
            >
              {stripMarkdown(m.content)}
            </p>
          </div>
        ))}
        {sending && <p className="text-sm text-black/40">Meminisse가 듣고 있어요...</p>}
        {error && <p className="text-sm text-black/50">{error}</p>}
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
          className="relative shrink-0 whitespace-nowrap rounded-full bg-black px-5 py-3 text-base text-white disabled:opacity-40"
        >
          <span aria-hidden className="pointer-events-none absolute inset-0">
            <RippleRings className="text-black/25" />
          </span>
          <span className="relative z-10">보내기</span>
        </button>
      </form>
    </div>
  );
}
