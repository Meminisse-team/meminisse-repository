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

const FALLBACK_OPENING_LINE = "오늘은 어떤 기억을 함께 떠올려볼까요? 편하게 말씀해주세요.";

/**
 * '오늘의 대화' 클릭 시 뜨는 채팅 컴포넌트. lib/api/interviews.ts를 통해 실제
 * 백엔드(Solar 기반 인터뷰 에이전트)와 대화한다 — 더미 데이터는 쓰지 않는다.
 * resumeSessionId가 있으면 그 세션의 chat_logs를 불러와 이어서 보여주고, 없으면
 * 첫 메시지를 보낼 때 비로소 세션을 만든다(빈 세션이 계속 쌓이는 것을 방지) —
 * 대신 세션을 만들기 전에도 GET next-preview로 "다음 질문이 뭔지"는 미리 보여준다
 * (2026-07-14: 이전엔 "어떤 대화를 해볼까요?" 같은 정적 문구만 보이던 문제 수정).
 */
export function ChatOverlay({ open, onClose, resumeSessionId, onSessionChanged }: ChatOverlayProps) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [linkedMediaAssetId, setLinkedMediaAssetId] = useState<string | null>(null);
  const [linkedPhoto, setLinkedPhoto] = useState<MediaAsset | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  /** 세션을 만들기 전 미리 보여준 인사말/다음 질문. messages와 별개로 항상 맨 위에
   * 고정 표시한다(messages.length===0일 때만 보이던 예전 방식은 첫 답변을 보내는
   * 순간 질문 자체가 화면에서 사라지는 문제가 있었다, 2026-07-14). 기존 세션을
   * 이어보는 경우엔 그 세션의 chat_logs 첫머리에 이미 질문이 들어있으므로 null로
   * 비워 중복 표시를 막는다. */
  const [openingLine, setOpeningLine] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 방금 세션 하나가 끝났고, 아직 "계속하기/오늘은 여기까지"를 선택하지 않은 상태.
   * 이 동안은 입력창 대신 두 버튼을 보여준다. */
  const [justCompleted, setJustCompleted] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // resumeSessionId를 effect의 반응형 의존성으로 두지 않기 위한 최신값 스냅샷.
  // 부모(DashboardHomePage)가 onSessionChanged로 세션 목록을 다시 불러오면
  // resumeSessionId prop 값이 바뀌는데(예: null -> 방금 만든 세션 id, 또는
  // 그 반대), effect가 resumeSessionId에도 반응하면 대화가 한창 진행 중일 때도
  // "세션을 새로 이어보는" 로직이 재실행돼 로컬 messages를 서버 스냅샷으로
  // 덮어써버렸다 — 꼬리 질문에 답하면 그동안의 대화 내역이 사라지는 버그로
  // 나타났다(2026-07-15). 대화창이 열리는 "그 순간"의 값만 한 번 쓰고, 열려있는
  // 동안의 세션 생성/완료/전환은 전부 컴포넌트 내부 상태(sessionId)로만 관리한다.
  const resumeSessionIdRef = useRef(resumeSessionId);
  useEffect(() => {
    resumeSessionIdRef.current = resumeSessionId;
  }, [resumeSessionId]);

  // 세션을 만들기 전 "다음엔 뭘 물을지" 미리보기를 새로 가져와 빈 대화창으로
  // 되돌린다 — 대화창을 처음 열 때와, "다음 이야기 계속하기" 버튼을 눌러 새
  // 세션으로 넘어갈 때 둘 다 이 함수 하나로 처리한다(2026-07-15 — 이전엔 버튼을
  // 눌러도 messages가 그대로 남아있어 한 세션 안에서 질문을 여러 개 받는 것처럼
  // 보이는 문제가 있었다. "다음 이야기 계속하기" = 진짜 새 채팅이 열리는 경험).
  function loadFreshPreview() {
    setMessages([]);
    setOpeningLine(FALLBACK_OPENING_LINE);
    setLinkedMediaAssetId(null);
    setLinkedPhoto(null);
    setLoading(true);
    interviewsApi
      .previewNext()
      .then((preview) => {
        setOpeningLine(preview.opening_message);
        if (preview.session_type === "photo") setLinkedMediaAssetId(preview.linked_media_asset_id);
      })
      .catch(() => setOpeningLine(FALLBACK_OPENING_LINE))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!open) return;
    const initialResumeId = resumeSessionIdRef.current;
    setError(null);
    setJustCompleted(false);
    setSessionId(initialResumeId);
    setLinkedMediaAssetId(null);
    setLinkedPhoto(null);
    if (!initialResumeId) {
      loadFreshPreview();
      return;
    }
    setOpeningLine(null);
    setLoading(true);
    interviewsApi
      .get(initialResumeId)
      .then((detail) => {
        setMessages(detail.chat_logs);
        setLinkedMediaAssetId(detail.session_type === "photo" ? detail.linked_media_asset_id : null);
      })
      .catch(() => setError("이전 대화를 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, [open]);

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

  useEffect(() => {
    // 여러 줄을 편하게 쓸 수 있도록 입력창 높이를 내용에 맞춰 자동으로 늘린다
    // (max-h-40로 상한을 두고 그 이상은 textarea 자체 스크롤로 처리).
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  if (!open) return null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
    setInput("");
    setError(null);

    // 낙관적 렌더링: 서버 왕복을 기다리지 않고 내가 보낸 말풍선부터 바로 띄운다 —
    // 이전엔 응답이 올 때까지 아무것도 안 뜨다가 내 메시지와 답변이 한꺼번에
    // 나타나서 "실제 채팅 같지 않다"는 문제가 있었다(2026-07-14). optimisticId는
    // 서버가 실제 id를 배정해 돌려주면 그걸로 교체한다.
    const optimisticId = `optimistic-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: optimisticId,
        session_id: sessionId ?? "",
        role: "user",
        content,
        turn_index: -1,
        created_at: new Date().toISOString(),
      },
    ]);
    setSending(true);
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
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== optimisticId),
        turn.user_message,
        turn.assistant_message,
      ]);
      if (turn.session.status !== "open") {
        // 이 세션은(질문 하나 분량의 슬롯이 다 채워져) 서버가 이미 완료 처리했다 —
        // 다음 발화는 반드시 새 세션을 만들어야 한다. 그대로 두면 완료된 세션에
        // 계속 발화가 쌓여 서버가 409로 거부하고(interview_service.py:
        // SessionNotOpenError), 예전엔 그 경로가 없어 매 턴마다 완료 처리가
        // 재실행되며 Phase 2 후처리(이벤트 추출)가 턴마다 중복 실행되는 문제가
        // 있었다(2026-07-14). "계속하기/오늘은 여기까지"를 고를 수 있게 입력창
        // 대신 버튼을 보여준다 — 계속하기를 누르면 그때 GET next-preview로 다음
        // 질문을 새로 가져온다(handleContinue 참조, 2026-07-15).
        setSessionId(null);
        setLinkedMediaAssetId(null);
        setLinkedPhoto(null);
        setJustCompleted(true);
        onSessionChanged?.(turn.session.id);
      }
    } catch {
      setMessages((prev) => prev.filter((m) => m.id !== optimisticId));
      setInput(content); // 실패 시 방금 쓴 내용을 잃지 않도록 입력창에 복구한다.
      setError("메시지를 보내지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSending(false);
    }
  }

  function handleContinue() {
    setSessionId(null);
    setJustCompleted(false);
    setError(null);
    loadFreshPreview();
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
        {error && <p className="text-sm text-black/50">{error}</p>}
      </div>

      {justCompleted ? (
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
        <form onSubmit={handleSubmit} className="flex items-end gap-3 border-t border-black/10 px-6 py-4">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Enter로 전송, Shift+Enter로 줄바꿈 — 여러 줄 입력을 자연스럽게 쓸 수 있게.
              if (e.key === "Enter" && !e.shiftKey) {
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
      )}
    </div>
  );
}
