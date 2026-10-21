"use client";
import { RippleRings } from "@/components/ui/RippleRings";
import type { useInterviewSession } from "../hooks/useInterviewSession";
type State = ReturnType<typeof useInterviewSession>;
export function ChatComposer({state}: {state: State}) {
 const {justCompleted, handleContinue, handleEnd, canSkip, handleSkip, skipping, sending, handleSubmit, textareaRef, input, setInput} = state;
 return (
justCompleted ? (
        <div className="flex items-center justify-center gap-3 border-t border-black/10 px-6 py-4">
          <button
            type="button"
            onClick={handleContinue}
            className="relative shrink-0 whitespace-nowrap rounded-full bg-black px-5 py-3 text-base text-white"
          >
            <span aria-hidden className="pointer-events-none absolute inset-0">
              <RippleRings className="text-black/25" />
            </span>
            <span className="relative z-10">다음 이야기 계속하기</span>
          </button>
          <button
            type="button"
            onClick={() => void handleEnd()}
            className="shrink-0 whitespace-nowrap rounded-full border border-black/15 px-5 py-3 text-base text-black hover:border-black"
          >
            오늘은 여기까지
          </button>
        </div>
      ) : (
        <div className="border-t border-black/10">
          {canSkip && (
            <div className="flex justify-center px-6 pt-3">
              <button
                type="button"
                onClick={() => void handleSkip()}
                disabled={skipping || sending}
                className="text-sm text-black/40 underline-offset-4 hover:text-black hover:underline disabled:opacity-50"
              >
                {skipping ? "다음 질문 가져오는 중..." : "이 질문 넘어가기"}
              </button>
            </div>
          )}
          <form onSubmit={handleSubmit} className="flex items-end gap-3 px-6 py-4">
            <textarea
              aria-label="이야기 입력"
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                // Enter로 전송, Shift+Enter로 줄바꿈 — 여러 줄 입력을 자연스럽게 쓸 수 있게.
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void handleSubmit(e);
                }
              }}
              rows={1}
              placeholder="그때 상황을 편하게, 떠오르는 대로 자세히 들려주세요"
              className="max-h-40 flex-1 resize-none overflow-y-auto rounded-2xl border border-black/15 px-5 py-3 text-base leading-relaxed outline-none placeholder:text-black/35 focus:border-black"
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
      )
);
}
