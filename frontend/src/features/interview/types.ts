export interface ChatOverlayProps {
  open: boolean;
  onClose: () => void;
  /** 이어갈 열린(open) 세션이 있으면 그 id, 없으면 null(첫 발화 시점에 새로 만든다). */
  resumeSessionId: string | null;
  /** 세션이 새로 생성되거나 종료돼 부모(대시보드 미리보기)가 갱신해야 할 때 호출. */
  onSessionChanged?: (sessionId: string) => void;
  /** "queue"(기본) = 자동 배정 큐(고정 질문/사진), "episode" = 사용자가 직접 시작하는
   * 자유 에피소드(대시보드 "에피소드 추가", 2026-07-16). 오버레이가 열려 있는 동안은
   * 고정값 — 부모가 여는 시점에만 정한다. */
  startMode?: "queue" | "episode";
}
