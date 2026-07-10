"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";

interface TypewriterProps {
  /** \n으로 줄바꿈을 표현한다. */
  text: string;
  /** 한 글자당 걸리는 시간(ms). */
  speed?: number;
  /** 시작 전 대기 시간(ms). */
  startDelay?: number;
  className?: string;
  onComplete?: () => void;
  /** 다 쓴 뒤에도 커서를 깜빡일지. */
  cursor?: boolean;
}

/**
 * 사람이 타이핑하는 것처럼 한 글자씩 나타나는 재사용 텍스트 컴포넌트.
 * 문자 노출 자체는 setInterval로 진행하고(글자 단위 애니메이션에는 이 방식이
 * framer-motion의 stagger보다 텍스트 길이 변화에 더 안정적이다), 깜빡이는
 * 커서는 framer-motion으로 처리한다.
 */
export function Typewriter({
  text,
  speed = 55,
  startDelay = 0,
  className = "",
  onComplete,
  cursor = true,
}: TypewriterProps) {
  const [displayed, setDisplayed] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    setDisplayed("");
    setDone(false);
    let index = 0;
    let intervalId: ReturnType<typeof setInterval> | undefined;

    const timeoutId = setTimeout(() => {
      intervalId = setInterval(() => {
        index += 1;
        setDisplayed(text.slice(0, index));
        if (index >= text.length) {
          if (intervalId) clearInterval(intervalId);
          setDone(true);
          onComplete?.();
        }
      }, speed);
    }, startDelay);

    return () => {
      clearTimeout(timeoutId);
      if (intervalId) clearInterval(intervalId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, speed, startDelay]);

  const lines = displayed.split("\n");

  return (
    <span className={className}>
      {lines.map((line, i) => (
        <span key={i} className="block">
          {line}
          {i === lines.length - 1 && cursor && !done && (
            <motion.span
              aria-hidden
              className="ml-1 inline-block h-[0.9em] w-[2px] translate-y-[0.1em] bg-current align-middle"
              animate={{ opacity: [1, 1, 0, 0] }}
              transition={{ duration: 0.9, repeat: Infinity, times: [0, 0.5, 0.5, 1], ease: "linear" }}
            />
          )}
        </span>
      ))}
    </span>
  );
}
