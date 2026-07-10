import { RippleRings } from "@/components/ui/RippleRings";

interface RippleLogoProps {
  size?: number;
  className?: string;
}

/** 대시보드 상단의 커다란 원형 로고. 물결이 항상 잔잔하게 퍼져나간다 — 시니어
 * 사용자에게 "지금 이 서비스가 조용히 듣고 있다"는 느낌을 주기 위한 무드 요소다. */
export function RippleLogo({ size = 148, className = "" }: RippleLogoProps) {
  return (
    <div
      className={`relative flex items-center justify-center ${className}`}
      style={{ width: size, height: size }}
    >
      <RippleRings className="text-black/15" />
      <div
        className="relative z-10 flex items-center justify-center rounded-full bg-black"
        style={{ width: size * 0.52, height: size * 0.52 }}
      >
        <span className="font-serif-kr text-2xl tracking-wide text-white">M</span>
      </div>
    </div>
  );
}
