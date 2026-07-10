import type { ButtonHTMLAttributes } from "react";

import { RippleRings } from "@/components/ui/RippleRings";

interface RippleTextButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  ringSize?: number;
}

/** 배경(네모) 없이 텍스트만 떠 있고, 주변으로 로고와 같은 파동이 퍼지는 버튼.
 * 환영 화면의 '다음' 버튼에 쓴다. */
export function RippleTextButton({
  children,
  className = "",
  ringSize = 112,
  ...props
}: RippleTextButtonProps) {
  return (
    <button
      type="button"
      className={`group relative inline-flex items-center justify-center text-lg font-medium tracking-wide text-black transition-opacity hover:opacity-70 ${className}`}
      {...props}
    >
      <span
        className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
        style={{ width: ringSize, height: ringSize }}
      >
        <RippleRings className="text-black/20" />
      </span>
      <span className="relative z-10">{children}</span>
    </button>
  );
}
