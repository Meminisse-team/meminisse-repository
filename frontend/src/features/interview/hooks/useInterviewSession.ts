"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { interviewsApi } from "../api/interviews";
import { mediaApi } from "@/features/media/api/media";
import type { ChatMessage, MediaAsset } from "@/types/api";
import type { ChatOverlayProps } from "../types";
const FALLBACK_OPENING_LINE = "오늘은 어떤 기억을 함께 떠올려볼까요? 편하게 말씀해주세요.";
const EPISODE_OPENING_LINE = "편하게 시작해볼까요? 들려주고 싶은 이야기를 자유롭게 적어주세요.";

export function useInterviewSession({
  open,
  onClose,
  resumeSessionId,
  onSessionChanged,
  startMode = "queue",
}: ChatOverlayProps) {
  const [sessionId, setSessionId] = useState<string | null>(resumeSessionId);
  /** 지금 이 대화가 실제로 어떤 타입인지(헤더 표시용) — resume 시엔 서버가 알려준
   * session_type, 새로 만든 직후엔 생성 응답의 session_type을 따른다. null이면
   * 아직 세션이 없는 상태라 startMode로 대신 판단한다(handleSubmit 이전). */
  const [activeSessionType, setActiveSessionType] = useState<"queue" | "episode" | null>(null);
  const [linkedMediaAssetId, setLinkedMediaAssetId] = useState<string | null>(null);
  const [linkedPhoto, setLinkedPhoto] = useState<MediaAsset | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  /** 세션을 만들기 전 미리 보여준 인사말/다음 질문. messages와 별개로 항상 맨 위에
   * 고정 표시한다(messages.length===0일 때만 보이던 예전 방식은 첫 답변을 보내는
   * 순간 질문 자체가 화면에서 사라지는 문제가 있었다, 2026-07-14). 기존 세션을
   * 이어보는 경우엔 그 세션의 chat_logs 첫머리에 이미 질문이 들어있으므로 null로
   * 비워 중복 표시를 막는다. */
  const [openingLine, setOpeningLine] = useState<string | null>(resumeSessionId ? null : startMode === "episode" ? EPISODE_OPENING_LINE : FALLBACK_OPENING_LINE);
  /** 미리보기 상태에서 실제로 배정할 다음 항목이 있는지(next-preview의 session_type이
   * null이 아닌지). '이 질문 넘어가기' 버튼 노출 판정에 쓴다 — 큐를 모두 마쳐
   * "준비된 질문에는 모두 답변해 주셨어요"가 보일 땐 건너뛸 대상이 없다. */
  const [hasNextItem, setHasNextItem] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [loading, setLoading] = useState(Boolean(resumeSessionId) || startMode !== "episode");
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
    setActiveSessionType(null);
    setLinkedMediaAssetId(null);
    setLinkedPhoto(null);
    setHasNextItem(false);
    if (startMode === "episode") {
      // 에피소드는 자동 배정 큐와 무관하므로 next-preview를 조회할 필요가 없다 —
      // 세션도 아직 안 만드니(첫 발화 시점에 생성) 로컬 문구만 바로 보여준다.
      setOpeningLine(EPISODE_OPENING_LINE);
      return;
    }
    setOpeningLine(FALLBACK_OPENING_LINE);
    setLoading(true);
    interviewsApi
      .previewNext()
      .then((preview) => {
        setOpeningLine(preview.opening_message);
        setHasNextItem(preview.session_type !== null);
        if (preview.session_type === "photo") setLinkedMediaAssetId(preview.linked_media_asset_id);
      })
      .catch(() => setOpeningLine(FALLBACK_OPENING_LINE))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!open) return;
    const initialResumeId = resumeSessionIdRef.current;
    if (!initialResumeId) {
      if (startMode === "episode") return;
      interviewsApi.previewNext().then(preview => {
        setOpeningLine(preview.opening_message);
        setHasNextItem(preview.session_type !== null);
        if (preview.session_type === "photo") setLinkedMediaAssetId(preview.linked_media_asset_id);
      }).catch(() => setError("다음 질문을 불러오지 못했어요."))
        .finally(() => setLoading(false));
      return;
    }
    interviewsApi
      .get(initialResumeId)
      .then((detail) => {
        setMessages(detail.chat_logs);
        setLinkedMediaAssetId(detail.session_type === "photo" ? detail.linked_media_asset_id : null);
        setActiveSessionType(detail.session_type === "episode" ? "episode" : "queue");
      })
      .catch(() => setError("이전 대화를 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, [open, startMode]);

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
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }, [messages]);

  useEffect(() => {
    // 여러 줄을 편하게 쓸 수 있도록 입력창 높이를 내용에 맞춰 자동으로 늘린다
    // (max-h-40로 상한을 두고 그 이상은 textarea 자체 스크롤로 처리).
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);


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
        // startMode가 "episode"면 큐와 무관한 자유 에피소드로 만든다. 큐 모드에서는
        // 서버가 상황에 따라 session_type을 PHOTO로 자동 전환할 수 있다(고정 질문
        // 큐와 사진 큐를 합쳐 다음 항목을 고르는 백엔드 오케스트레이션, docs/
        // QUESTION_BANK_GUIDE.md 5절 참조) — 요청 바디는 그대로 fixed_question이어도 무방하다.
        const created = await interviewsApi.create({
          session_type: startMode === "episode" ? "episode" : "fixed_question",
        });
        activeSessionId = created.id;
        setSessionId(created.id);
        setLinkedMediaAssetId(created.session_type === "photo" ? created.linked_media_asset_id : null);
        setActiveSessionType(created.session_type === "episode" ? "episode" : "queue");
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

  // 지금 화면의 질문에 아직 아무 답도 하지 않은 상태에서만 '이 질문 넘어가기'를
  // 보여준다 — 답변을 시작한 뒤에 건너뛰면 이미 쓴 내용이 통째로 버려지므로
  // (SKIPPED 세션은 산문 재조립 대상이 아님) 그 상태에선 버튼을 숨긴다.
  // 에피소드는 배정된 질문 자체가 없어 건너뛸 대상이 없다.
  const canSkip =
    (activeSessionType ?? startMode) !== "episode" &&
    !justCompleted &&
    !loading &&
    !messages.some((m) => m.role === "user") &&
    (sessionId !== null || hasNextItem);

  async function handleSkip() {
    if (skipping || sending) return;
    setSkipping(true);
    setError(null);
    try {
      if (sessionId) {
        // 세션이 이미 만들어진 뒤(답변 실패 후 재개 등 드문 경우) — 그 세션을
        // SKIPPED로 종료하고 다음 미리보기를 새로 가져온다.
        const skippedId = sessionId;
        await interviewsApi.skip(skippedId);
        setSessionId(null);
        onSessionChanged?.(skippedId);
        loadFreshPreview();
      } else {
        // 일반적인 경로: 아직 세션이 없는 미리보기 상태 — 서버가 그 항목을
        // 건너뛰고 다음 미리보기를 한 번에 돌려준다.
        const preview = await interviewsApi.skipNext();
        setMessages([]);
        setLinkedMediaAssetId(null);
        setLinkedPhoto(null);
        setOpeningLine(preview.opening_message);
        setHasNextItem(preview.session_type !== null);
        if (preview.session_type === "photo") setLinkedMediaAssetId(preview.linked_media_asset_id);
      }
    } catch {
      setError("질문을 넘어가지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSkipping(false);
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


  return { activeSessionType, linkedPhoto, messages, openingLine, input, setInput, sending, skipping, loading, error, justCompleted, listRef, textareaRef, canSkip, handleSubmit, handleSkip, handleContinue, handleEnd };
}
