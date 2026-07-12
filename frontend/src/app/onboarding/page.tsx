"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { StepperDots } from "@/components/ui/StepperDots";
import { Typewriter } from "@/components/ui/Typewriter";
import { authApi } from "@/lib/api/auth";
import { usersApi } from "@/lib/api/users";
import { ApiError } from "@/lib/api/client";
import { oauthPendingProfile } from "@/lib/auth/oauthPendingProfile";
import { session } from "@/lib/auth/session";
import { signupDraft, type SignupDraft } from "@/lib/auth/signupDraft";

const TOTAL_STEPS = 4;
const CONSENT_NOTICE_VERSION = "v1";

/** 이메일/비밀번호 가입과 소셜 로그인 둘 다 이 화면을 함께 쓴다 — 물어볼 내용
 * (생년/고향/동의)은 같지만 "계정을 언제, 어떻게 만드는지"가 다르다:
 * - 이메일/비밀번호: 계정이 아직 없다. 이 화면 끝에서 POST /users로 한 번에 생성.
 * - 소셜 로그인: 이미 로그인된 상태다(auth/callback에서 oauth-sync 완료). 이름도
 *   이미 알고 있으니 다시 안 묻고, 끝에서 PATCH /users/{id}로 채우기만 한다. */
type OnboardingSource =
  | { kind: "password"; draft: SignupDraft }
  | { kind: "oauth"; name: string; userId: string };

export default function OnboardingPage() {
  const router = useRouter();
  const [source, setSource] = useState<OnboardingSource | null>(null);
  const [step, setStep] = useState(0);
  const [birthYear, setBirthYear] = useState("");
  const [hometown, setHometown] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const draft = signupDraft.get();
    if (draft) {
      setSource({ kind: "password", draft });
      return;
    }
    const pending = oauthPendingProfile.get();
    const userId = session.getUserId();
    if (pending && userId) {
      setSource({ kind: "oauth", name: pending.name, userId });
      return;
    }
    router.replace("/");
  }, [router]);

  if (!source) return null;
  const displayName = source.kind === "password" ? source.draft.name : source.name;

  async function handleFinish() {
    if (!source) return;
    setError(null);
    setSubmitting(true);
    try {
      let userId: string;
      if (source.kind === "password") {
        const user = await usersApi.create({
          email: source.draft.email,
          password: source.draft.password,
          name: source.draft.name,
          birth_year: birthYear ? Number(birthYear) : undefined,
          hometown: hometown || undefined,
        });
        const { access_token, refresh_token } = await authApi.login({
          email: source.draft.email,
          password: source.draft.password,
        });
        session.setTokens(access_token, refresh_token);
        session.setUserId(user.id);
        userId = user.id;
      } else {
        // 이미 로그인돼 있다 — 생년/고향만 채운다(값을 안 넣은 필드는 그대로 유지).
        await usersApi.updateProfile(source.userId, {
          birth_year: birthYear ? Number(birthYear) : undefined,
          hometown: hometown || undefined,
        });
        userId = source.userId;
      }

      await usersApi.createConsent(userId, {
        consent_type: "data_collection",
        notice_version: CONSENT_NOTICE_VERSION,
        granted_by: "self",
      });

      signupDraft.clear();
      oauthPendingProfile.clear();
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("이미 가입된 이메일이에요. 처음 화면에서 로그인해주세요.");
      } else {
        setError("가입 중 문제가 생겼어요. 잠시 후 다시 시도해주세요.");
      }
      setSubmitting(false);
    }
  }

  function goNext() {
    if (step < TOTAL_STEPS - 1) {
      setStep((s) => s + 1);
    } else {
      void handleFinish();
    }
  }

  return (
    <main className="flex flex-1 flex-col justify-between px-8 py-16 sm:px-16">
      <div className="max-w-xl">
        {step === 0 && (
          <Typewriter
            key="step0"
            text={`${displayName}님, 만나서 반가워요.\n당신에 대해 조금 더 알려주시겠어요?`}
            className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
          />
        )}
        {step === 1 && (
          <>
            <Typewriter
              key="step1"
              text={"태어난 해를 알려주세요."}
              className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
            />
            <input
              type="number"
              inputMode="numeric"
              value={birthYear}
              onChange={(e) => setBirthYear(e.target.value)}
              placeholder="예: 1955"
              className="mt-10 w-48 border-b border-black/20 bg-transparent px-1 py-3 text-2xl outline-none placeholder:text-black/30 focus:border-black"
            />
          </>
        )}
        {step === 2 && (
          <>
            <Typewriter
              key="step2"
              text={"어디에서 나고 자라셨나요?"}
              className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
            />
            <input
              value={hometown}
              onChange={(e) => setHometown(e.target.value)}
              placeholder="예: 부산"
              className="mt-10 w-64 border-b border-black/20 bg-transparent px-1 py-3 text-2xl outline-none placeholder:text-black/30 focus:border-black"
            />
          </>
        )}
        {step === 3 && (
          <>
            <Typewriter
              key="step3"
              text={"소중한 이야기를 기록하기 위해\n동의가 필요해요."}
              className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
            />
            <label className="mt-10 flex max-w-md items-start gap-3 text-base leading-relaxed text-black/75">
              <input
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                className="mt-1 h-5 w-5 accent-black"
              />
              대화 내용과 사진을 자서전 제작 목적으로 수집·이용하는 데 동의합니다.
              (수집된 원문은 최종본 확정 후 일정 기간이 지나면 삭제돼요.)
            </label>
          </>
        )}

        {error && <p className="mt-6 max-w-md text-base text-black/60">{error}</p>}
      </div>

      <div className="flex items-center justify-between">
        <StepperDots steps={TOTAL_STEPS} current={step} />
        <Button
          onClick={goNext}
          disabled={
            submitting ||
            (step === 3 && !agreed) ||
            (step === 1 && birthYear !== "" && Number(birthYear) < 1900)
          }
        >
          {step < TOTAL_STEPS - 1 ? "다음" : submitting ? "시작하는 중..." : "동의하고 시작하기"}
        </Button>
      </div>
    </main>
  );
}
