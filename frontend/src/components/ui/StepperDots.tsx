interface StepperDotsProps {
  steps: number;
  /** 0-indexed 현재 단계. */
  current: number;
  className?: string;
}

/** 온보딩처럼 여러 단계를 순서대로 밟는 화면의 진행 상황 표시. */
export function StepperDots({ steps, current, className = "" }: StepperDotsProps) {
  return (
    <div className={`flex items-center gap-2 ${className}`} role="progressbar" aria-valuenow={current + 1} aria-valuemin={1} aria-valuemax={steps}>
      {Array.from({ length: steps }).map((_, i) => (
        <span
          key={i}
          className={`h-1.5 rounded-full transition-all duration-300 ${
            i === current ? "w-7 bg-black" : "w-1.5 bg-black/15"
          }`}
        />
      ))}
    </div>
  );
}
