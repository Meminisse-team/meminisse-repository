import type { ButtonHTMLAttributes } from "react";

import { RippleRings } from "@/components/ui/RippleRings";

type ButtonVariant = "primary" | "secondary";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "bg-black text-white hover:bg-black/80",
  secondary: "bg-black/5 text-black hover:bg-black/10",
};

const RING_CLASSES: Record<ButtonVariant, string> = {
  primary: "text-black/25",
  secondary: "text-black/15",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

/**
 * 시니어 친화 입력 설계(기획안 5절): 노안을 고려한 대형 폰트·고대비 UI를 기본값으로 삼는다.
 * 온보딩(개인정보 입력)의 '다음' 버튼과 같은 파동(RippleRings) 효과를 앱 전역 CTA 버튼에
 * 공통 적용하기 위해, 버튼 자신을 기준 박스(position: relative)로 삼아 그 안에 링을
 * inset-0으로 겹치고 바깥으로 퍼져나가게 한다(버튼의 실제 크기/모양에 자동으로 맞춰짐).
 */
export function Button({ variant = "primary", className = "", children, ...props }: ButtonProps) {
  return (
    <button
      className={`relative rounded-lg px-6 py-3 text-lg font-medium transition-colors disabled:opacity-50 ${VARIANT_CLASSES[variant]} ${className}`}
      {...props}
    >
      <span aria-hidden className="pointer-events-none absolute inset-0">
        <RippleRings className={RING_CLASSES[variant]} />
      </span>
      <span className="relative z-10">{children}</span>
    </button>
  );
}
