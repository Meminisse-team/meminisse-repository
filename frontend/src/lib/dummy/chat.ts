/**
 * '오늘의 대화' 더미 데이터. 백엔드에 "세션 목록 조회" 엔드포인트가 아직 없어서
 * (docs/API_ENDPOINTS.md "세션 히스토리 조회 엔드포인트 없음" 참조) 재방문 시
 * 이어보는 화면을 지금은 더미로 구성한다. 실제 메시지 전송 자체는
 * lib/api/interviews.ts로 이미 연동 가능하니, 이 더미를 실제 세션 목록으로
 * 교체하는 것이 프론트 다음 작업 우선순위 중 하나다.
 */

export interface DummyChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
}

export const dummyLastSessionPreview =
  "어머니께서 스무 살 무렵 처음 서울에 올라오셨던 이야기를 들려주셨어요...";

export const dummyChatHistory: DummyChatMessage[] = [
  {
    id: "d1",
    role: "assistant",
    content: "지난번엔 스무 살 무렵 서울에 올라오셨던 이야기를 들려주셨어요. 그때 가장 먼저 눈에 들어온 풍경이 있으셨을까요?",
    createdAt: "2026-07-08T10:02:00+09:00",
  },
  {
    id: "d2",
    role: "user",
    content: "서울역 앞에 사람이 그렇게 많은 걸 처음 봤지. 다들 어디로 그렇게 바쁘게 가는지 신기했어.",
    createdAt: "2026-07-08T10:03:12+09:00",
  },
  {
    id: "d3",
    role: "assistant",
    content: "정말 인상 깊으셨겠어요. 그때 함께 계셨던 분이 있으셨나요, 아니면 혼자셨나요?",
    createdAt: "2026-07-08T10:03:40+09:00",
  },
];

export const assistantOpeningLine =
  "오늘은 어떤 기억을 함께 떠올려볼까요? 편하게 말씀해주세요.";
