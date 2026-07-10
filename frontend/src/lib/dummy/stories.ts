/**
 * '나의 이야기' 더미 데이터. 실제로는 완료된 InterviewSession.session_prose(산문)와
 * Event.one_line_summary(사건 단위 요약)를 조합해 구성해야 하는데, 세션 단위
 * "한 줄 요약"과 "세션 목록 조회" 둘 다 백엔드에 아직 없어 지금은 더미로 둔다.
 */

export interface DummyStory {
  id: string;
  date: string;
  summary: string;
  prose: string;
}

export const dummyStories: DummyStory[] = [
  {
    id: "s1",
    date: "2026-07-08",
    summary: "스무 살, 처음 서울에 올라오던 날",
    prose:
      "그날은 아침부터 비가 조금 왔지. 서울역 앞에 내려서니 사람이 어찌나 많던지, 다들 어디로 그렇게 바쁘게 가는지 신기하기만 했어. 짐 보따리 하나 들고 낯선 골목을 두리번거리던 그 순간이 아직도 눈에 선하네.",
  },
  {
    id: "s2",
    date: "2026-07-03",
    summary: "첫 월급으로 부모님께 드린 내복",
    prose:
      "첫 월급을 받던 날, 제일 먼저 시장에 가서 내복 두 벌을 샀어. 어머니 아버지 손에 쥐어드리던 그 순간, 부끄러우면서도 뿌듯했던 기억이 나.",
  },
  {
    id: "s3",
    date: "2026-06-27",
    summary: "결혼식 전날 밤, 어머니와 나눈 대화",
    prose:
      "결혼식 전날 밤에 어머니가 내 손을 꼭 잡고 오래 이야기를 나눠주셨지. 살면서 힘든 날도 있을 거라고, 그럴 때마다 서로 마주 보고 웃으라고 하셨어.",
  },
];
