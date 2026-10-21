"use client";
import { stripMarkdown } from "@/lib/format/stripMarkdown";
import type { useInterviewSession } from "../hooks/useInterviewSession";
type State = ReturnType<typeof useInterviewSession>;
export function ChatMessageList({state}: {state: State}) {
 const {linkedPhoto, loading, openingLine, messages, sending, error, listRef} = state;
 return (
      <div role="log" aria-live="polite" aria-relevant="additions" ref={listRef} className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
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
        {!loading && openingLine && (
          <div className="flex justify-start">
            <p className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-black/5 px-4 py-3 text-base leading-relaxed text-black">
              {openingLine}
            </p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <p
              className={`max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-base leading-relaxed ${
                m.role === "user" ? "bg-black text-white" : "bg-black/5 text-black"
              }`}
            >
              {stripMarkdown(m.content)}
            </p>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <p className="max-w-[75%] animate-pulse rounded-2xl bg-black/5 px-4 py-3 text-base leading-relaxed text-black/50">
              Meminisse가 생각하고 있어요...
            </p>
          </div>
        )}
        {error && <p role="alert" className="text-sm text-black/50">{error}</p>}
      </div>

);
}
