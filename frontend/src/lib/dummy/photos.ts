/**
 * '사진첩' 더미 데이터. 백엔드에 미디어 자산 "목록" 조회 엔드포인트가 아직 없다
 * (POST /api/v1/media-assets 업로드만 존재). 최대 20장 제한은 프론트에서
 * 우선 흉내 내고, 실제 업로드는 이 더미 배열에만 반영한다.
 */

export const MAX_PHOTOS = 20;

export interface DummyPhoto {
  id: string;
  caption: string;
  takenYear: number | null;
  colorFrom: string;
  colorTo: string;
}

const GRADIENTS: [string, string][] = [
  ["#f5f5f4", "#e7e5e4"],
  ["#f0f0ef", "#dedede"],
  ["#f6f1ea", "#e8ddcf"],
  ["#eef0ee", "#dbe0db"],
];

export const dummyPhotos: DummyPhoto[] = [
  { id: "p1", caption: "부산 자갈치시장 앞에서", takenYear: 1975, colorFrom: GRADIENTS[0][0], colorTo: GRADIENTS[0][1] },
  { id: "p2", caption: "첫 직장 동료들과", takenYear: 1981, colorFrom: GRADIENTS[1][0], colorTo: GRADIENTS[1][1] },
  { id: "p3", caption: "결혼식 날", takenYear: 1988, colorFrom: GRADIENTS[2][0], colorTo: GRADIENTS[2][1] },
  { id: "p4", caption: "첫째 아이 백일", takenYear: 1990, colorFrom: GRADIENTS[3][0], colorTo: GRADIENTS[3][1] },
];

export function nextGradient(index: number): [string, string] {
  return GRADIENTS[index % GRADIENTS.length];
}
