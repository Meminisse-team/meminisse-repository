"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import { ApiError } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type { Autobiography, ChapterDraft } from "@/types/api";
import { chapterFlagCount } from "../utils/chapterFlags";


import { useAutobiographyJob } from "./useAutobiographyJob";
import { useAutobiographyPolling } from "./useAutobiographyPolling";
export function useAutobiography() {
  const { user } = useCurrentUser();
  const [autobiography, setAutobiography] = useState<Autobiography | null>(null);
  const [chapters, setChapters] = useState<ChapterDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [requestBusy, setBusy] = useState(false);
  const { job, consolidateTriggered, finalizeTriggered, pdfTriggered, chaptersWriteTriggered, setConsolidateTriggered, setFinalizeTriggered, setPdfTriggered, setChaptersWriteTriggered } = useAutobiographyJob();
  const busy = requestBusy || job !== "idle";
  // "각 장 집필 시작"을 눌렀는지 — 개별 챕터에 "생성 중" 신호가 없어서(백엔드에
  // is_generating 같은 필드가 없음), 이 화면 방문 동안 사용자가 실제로 집필을
  // 트리거했는지를 프론트에서만 기억해 미완료 챕터에 "집필하고 있어요..." 표시를
  // 보여준다(새로고침하면 초기화되지만, Promise.all로 전 챕터를 한 번에 큐잉하는
  // 현재 방식과 맞물려 이 화면에 머무는 동안은 항상 정확하다).
  // "확인 필요 N곳" 배지를 사용자가 직접 읽어보고 넘겨도 되겠다고 표시한 챕터
  // id 집합. 백엔드 데이터(factcheck_report/groundedness_report)는 그대로 두고
  // 화면에서만 감추는 것이라 새로고침하면 다시 나타난다 — 영구적으로 지우려면
  // 별도 백엔드 필드가 필요해 이번 범위에서는 프론트 상태로만 구현했다.
  const [acknowledgedChapterIds, setAcknowledgedChapterIds] = useState<Set<string>>(new Set());
  // "이 챕터 다시 쓰기"를 누른 챕터들. 다시 쓰는 챕터는 본문이 이미 있어서
  // (content !== null) 완료를 content로 감지할 수 없다 — 트리거 시점의
  // updated_at을 기억해 두고, 폴링으로 받은 updated_at이 달라지면 완료로 본다.
  const [rewritingChapters, setRewritingChapters] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState<number | null>(null);
  const pdfVersionAtStart = useRef<string | null>(null);
  const load = useCallback(async () => {
    if (!user) return;
    const bio = await autobiographiesApi.get(user.id);
    setAutobiography(bio);
    const hasSelectedToc = bio.toc_data?.selected_candidate_index != null;
    if (hasSelectedToc) {
      // final_content가 생긴 뒤에도 챕터 목록은 계속 쓴다 — 수록 사진 배치 단계가
      // "몇 장에 넣을지" 선택지를 그리는 재료다(PhotoPlacementPanel 참조).
      const list = await autobiographiesApi.listChapters(bio.id);
      setChapters(list);
      if (list.length > 0 && list.every((c) => c.content !== null)) {
        setChaptersWriteTriggered(false);
      }
      // 다시 쓰기가 끝난 챕터(updated_at이 트리거 시점과 달라짐)는 목록에서 뺀다.
      setRewritingChapters((prev) => {
        if (prev.size === 0) return prev;
        const next = new Map(prev);
        for (const chapter of list) {
          const triggeredAt = next.get(chapter.id);
          if (triggeredAt !== undefined && chapter.updated_at !== triggeredAt) {
            next.delete(chapter.id);
          }
        }
        return next.size === prev.size ? prev : next;
      });
    } else {
      setChapters([]);
    }
    if (bio.final_content) setFinalizeTriggered(false);
    if (bio.status !== "in_progress") setConsolidateTriggered(false);
    if (bio.pdf_version && bio.pdf_version !== pdfVersionAtStart.current) setPdfTriggered(false);
    return bio;
  }, [user, setChaptersWriteTriggered, setConsolidateTriggered, setFinalizeTriggered, setPdfTriggered]);

  // 폴링 틱마다 자서전 전체(final_content 수만 자)와 챕터 본문 전체를 통째로
  // 다시 받아오면, 몇 시간씩 이어지는 대기 시간 동안 Supabase egress가
  // 쌓인다(2026-07-19, 무료 등급 Egress 한도 초과 사고 원인 중 하나로 확인).
  // 그래서 폴링 틱은 경량 상태 조회(getPollingStatus)만 먼저 확인하고, 실제로
  // 뭔가 완료된 게 감지될 때만(챕터 본문 완성, 최종본 완성, PDF 생성, 상태
  // 전환) 기존 전체 조회(load)를 딱 한 번 호출한다. 최신 autobiography/
  // chapters/rewritingChapters는 ref로 읽는다 — pollLoad를 [load]에만
  // 의존하게 해 identity를 안정적으로 유지해야 하기 때문이다(startPolling이
  // 이미 실행 중인 인터벌은 교체하지 않고 최초 클로저를 그대로 쓰므로, 이
  // 함수가 상태가 바뀔 때마다 새로 만들어지면 그 인터벌은 영원히 오래된
  // 클로저만 보게 된다).
  useEffect(() => {
    if (!user) return;
    // loading 초기값이 true라 여기서 다시 setLoading(true)를 부를 필요가 없다 —
    // effect 안의 직접 setState는 불필요한 리렌더를 만든다(react-hooks/set-state-in-effect).
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load updates state only after awaited HTTP responses.
    load()
      .catch(() => setError("자서전 정보를 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, [user, load]);

  useAutobiographyPolling({ autobiography, chapters,
    enabled: job !== "idle" || rewritingChapters.size > 0 || chapters.some(c => c.content === null),
    onChanged: load, onError: setError,
  });

  async function handleConsolidate() {
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      await autobiographiesApi.consolidate(user.id);
      setConsolidateTriggered(true);
    } catch {
      setError("이야기를 정리하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateToc() {
    if (!autobiography) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await autobiographiesApi.generateToc(autobiography.id);
      // TOC 재생성 성공 — 기존 챕터 초안은 무효가 되므로 프론트 상태도 초기화한다.
      setChapters([]);
      setAutobiography(updated);
    } catch (err) {
      console.error("[handleGenerateToc] error:", err);
      if (err instanceof ApiError && err.status === 409) {
        setError("아직 목차를 만들 준비가 안 됐어요. 대화를 조금 더 나눠주세요.");
      } else {
        setError(
          `목차를 만들지 못했어요. 잠시 후 다시 시도해주세요.${
            err instanceof Error ? " (" + err.message + ")" : ""
          }`,
        );
      }
    } finally {
      setBusy(false);
    }
  }

  // 목차를 이미 선택한 뒤(ChapterProgress/FinalManuscript 화면)에도 "목차부터 다시"
  // 만들 수 있는 입구 — 예전에는 목차 선택이 한 번 하면 끝이라는 전제로 이 화면
  // 분기(아래 렌더링부의 selectedIndex 기준 삼항)에 재진입 경로가 아예 없었다
  // (2026-07-20 발견). 백엔드(generate_toc_candidates)는 재호출 시 이미
  // toc_data.selected_candidate_index를 null로 되돌려주므로, 그 결과를
  // setAutobiography에 반영하기만 하면 화면이 자동으로 TocSelection으로
  // 돌아간다 — handleGenerateToc 자체는 그대로 재사용하고, 여기서는 "지금까지
  // 쓴 챕터가 전부 사라진다"는 확인만 앞에 붙인다(다시 쓰기 handleRewriteChapter의
  // window.confirm 패턴과 동일).
  async function handleRegenerateToc() {
    if (
      !window.confirm(
        "목차를 다시 만들면 지금까지 쓴 챕터 본문이 모두 사라지고, 새 목차를 고른 뒤 처음부터 다시 써야 해요. 계속할까요?"
      )
    ) {
      return;
    }
    await handleGenerateToc();
  }

  async function handleSelectToc(index: number) {
    if (!autobiography) return;
    setSelecting(index);
    setError(null);
    try {
      const updated = await autobiographiesApi.selectToc(autobiography.id, index);
      setAutobiography(updated);
      const list = await autobiographiesApi.listChapters(updated.id);
      setChapters(list);
    } catch {
      setError("목차를 선택하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setSelecting(null);
    }
  }

  async function handleWriteAll() {
    if (!autobiography) return;
    const unwritten = chapters.filter((c) => c.content === null);
    if (unwritten.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await Promise.all(unwritten.map((c) => autobiographiesApi.writeChapter(autobiography.id, c.id)));
      setChaptersWriteTriggered(true);
    } catch {
      setError("챕터 집필을 시작하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRewriteChapter(chapter: ChapterDraft) {
    if (!autobiography) return;
    if (
      !window.confirm(
        `${chapter.chapter_index}장을 처음부터 다시 쓸까요? 현재 본문은 새로 쓴 내용으로 대체돼요.`
      )
    ) {
      return;
    }
    setError(null);
    try {
      await autobiographiesApi.writeChapter(autobiography.id, chapter.id);
      setRewritingChapters((prev) => new Map(prev).set(chapter.id, chapter.updated_at));
    } catch {
      setError("챕터 다시 쓰기를 시작하지 못했어요. 잠시 후 다시 시도해주세요.");
    }
  }

  async function handleFinalize() {
    if (!autobiography) return;
    // "확인했어요"를 누른 챕터는 사용자가 이미 검토했다는 뜻이므로 이 경고에서 뺀다.
    const flaggedCount = chapters
      .filter((c) => !acknowledgedChapterIds.has(c.id))
      .reduce((sum, c) => sum + chapterFlagCount(c), 0);
    if (
      flaggedCount > 0 &&
      !window.confirm(
        `아직 확인이 필요하다고 표시된 부분이 ${flaggedCount}곳 있어요. 그래도 최종본을 만들까요?`
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await autobiographiesApi.finalize(autobiography.id);
      setFinalizeTriggered(true);
    } catch {
      setError("최종본을 만들지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGeneratePdf() {
    if (!autobiography) return;
    setBusy(true);
    setError(null);
    try {
      await autobiographiesApi.generatePdf(autobiography.id);
      pdfVersionAtStart.current = autobiography.pdf_version;
      setPdfTriggered(true);
    } catch {
      setError("PDF를 만들지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }


  return { autobiography, setAutobiography, chapters, setChapters, loading, consolidateTriggered, finalizeTriggered, pdfTriggered, chaptersWriteTriggered, busy, acknowledgedChapterIds, setAcknowledgedChapterIds, rewritingChapters, error, selecting, handleConsolidate, handleGenerateToc, handleRegenerateToc, handleSelectToc, handleWriteAll, handleRewriteChapter, handleFinalize, handleGeneratePdf };
}
