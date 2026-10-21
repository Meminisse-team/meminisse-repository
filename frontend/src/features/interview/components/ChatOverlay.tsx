"use client";
import { useEffect, useRef } from "react";
import type { ChatOverlayProps } from "../types";
import { useInterviewSession } from "../hooks/useInterviewSession";
import { ChatMessageList } from "./ChatMessageList";
import { ChatComposer } from "./ChatComposer";
export function ChatOverlay(props: ChatOverlayProps) {
 return props.open ? <InterviewChat {...props} /> : null;
}
function InterviewChat(props: ChatOverlayProps) {
 const state = useInterviewSession(props);
 const { activeSessionType, handleEnd } = state;
 const { startMode = "queue" } = props;
 const dialogRef = useRef<HTMLDivElement>(null);
 useEffect(() => {
   const previous = document.activeElement as HTMLElement | null;
   dialogRef.current?.querySelector<HTMLElement>("textarea, button")?.focus();
   return () => previous?.focus();
 }, []);
  return (
    <div ref={dialogRef} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); void handleEnd(); }
      if (event.key === "Tab") {
        const nodes = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled]), [href], [tabindex="0"]')];
        const first = nodes[0], last = nodes.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }} role="dialog" aria-modal="true" aria-label="추억 인터뷰" className="fixed inset-0 z-50 flex flex-col bg-white animate-fade-up">
      <header className="flex items-center justify-between border-b border-black/10 px-6 py-4">
        <span className="font-serif-kr text-lg text-black">
          {(activeSessionType ?? startMode) === "episode" ? "에피소드" : "오늘의 대화"}
        </span>
        <button
          type="button"
          onClick={() => void handleEnd()}
          className="text-base text-black/60 underline-offset-4 hover:text-black hover:underline"
        >
          대화 종료
        </button>
      </header>

      <ChatMessageList state={state} />

      <ChatComposer state={state} />
    </div>
  );
}
