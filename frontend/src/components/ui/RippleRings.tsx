interface RippleRingsProps {
  /** 동시에 퍼져나가는 링 개수. */
  count?: number;
  /** 링 색(테두리) — currentColor 상속. Tailwind text-* 클래스로 부모에서 지정. */
  className?: string;
}

/**
 * 동그란 테두리가 물결처럼 부드럽게 퍼져나가는 CSS 애니메이션(globals.css의
 * .ripple-ring 키프레임 참조). 로고(RippleLogo)와 텍스트 버튼(RippleTextButton)이
 * 공유하는 시각 요소라 별도 컴포넌트로 분리했다. 부모에 position: relative가 있고
 * 정사각형 크기가 잡혀 있어야 한다(각 링은 inset-0으로 부모를 꽉 채운 뒤 확대된다).
 */
export function RippleRings({ count = 3, className = "" }: RippleRingsProps) {
  const delayStep = 1.1;
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <span
          key={i}
          aria-hidden
          className={`ripple-ring ${className}`}
          style={{ animationDelay: `${i * delayStep}s` }}
        />
      ))}
    </>
  );
}
