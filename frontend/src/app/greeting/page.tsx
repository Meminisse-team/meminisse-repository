"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { RippleTextButton } from "@/components/ui/RippleTextButton";
import { Typewriter } from "@/components/ui/Typewriter";
import { signupDraft } from "@/lib/auth/signupDraft";

const GREETING_TEXT =
  "안녕하세요, 대필가 Meminisse입니다.\n당신의 기억의 바다로 빠져들어볼까요?";

export default function GreetingPage() {
  const router = useRouter();
  const [showNext, setShowNext] = useState(false);

  useEffect(() => {
    // 진입화면(회원가입)을 거치지 않고 곧바로 이 주소로 들어온 경우를 막는다.
    if (!signupDraft.get()) {
      router.replace("/");
    }
  }, [router]);

  return (
    <main className="flex flex-1 flex-col justify-between px-8 py-16 sm:px-16">
      <div className="max-w-xl">
        <Typewriter
          text={GREETING_TEXT}
          speed={65}
          startDelay={300}
          onComplete={() => setShowNext(true)}
          className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
        />
      </div>

      <div
        className={`flex justify-end transition-opacity duration-700 ${
          showNext ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      >
        <RippleTextButton onClick={() => router.push("/onboarding")}>다음</RippleTextButton>
      </div>
    </main>
  );
}
