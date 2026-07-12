/**
 * 채팅 메시지를 일반 대화체 텍스트로 표시하기 위한 방어적 마크다운 제거.
 *
 * 백엔드 프롬프트(app/agents/prompts.py의 INTERVIEW_PERSONA_SYSTEM_PROMPT·
 * FOLLOWUP_SYSTEM_PROMPT)에 마크다운을 쓰지 말라는 지침을 넣어뒀지만, LLM 출력은
 * 100% 보장되지 않고 이미 저장된 과거 대화 로그에도 마크다운이 남아있을 수 있어
 * 렌더링 시점에도 한 번 더 걷어낸다. 리스트/헤더까지 손댈 필요는 없다 — 실제로
 * 관찰된 건 **강조** 표기뿐이다(구조가 있는 서식은 목차/자서전 쪽이지 대화 쪽이 아님).
 */
export function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "$1");
}
