"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { StepperDots } from "@/components/ui/StepperDots";
import { Typewriter } from "@/components/ui/Typewriter";
import { authApi } from "@/lib/api/auth";
import { usersApi } from "@/lib/api/users";
import { ApiError } from "@/lib/api/client";
import { legalApi } from "@/lib/api/legal";
import { session } from "@/lib/auth/session";
import { signupDraft, type SignupDraft } from "@/lib/auth/signupDraft";
import type { EducationLevel, MaritalStatus } from "@/types/api";

const TOTAL_STEPS = 7;
const CONSENT_NOTICE_VERSION = "v1";

// 셋 다 선택 응답이다 — 학력·혼인여부·자녀유무를 가입 시점부터 캐묻는 게 부담일
// 수 있어(2026-07-16 설계), 각 단계마다 "응답하지 않음"을 명시적인 선택지로
// 둔다. 대화 내용에서 추론하지 않고 이 라디오 버튼 응답만으로 동적 질문
// 필터링(backend/app/data/question_bank.py의 eligibility)을 판정한다.
const EDUCATION_OPTIONS: { value: EducationLevel; label: string }[] = [
  { value: "elementary", label: "초등학교 졸업" },
  { value: "middle_school", label: "중학교 졸업" },
  { value: "high_school", label: "고등학교 졸업" },
  { value: "university", label: "대학교 졸업" },
  { value: "graduate_school", label: "대학원 졸업" },
];

const MARITAL_OPTIONS: { value: MaritalStatus; label: string }[] = [
  { value: "single", label: "미혼" },
  { value: "married", label: "기혼" },
  { value: "divorced", label: "이혼" },
  { value: "widowed", label: "사별" },
];

const CHILDREN_OPTIONS: { value: "yes" | "no"; label: string }[] = [
  { value: "yes", label: "있음" },
  { value: "no", label: "없음" },
];

function RadioGroup<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T | "";
  onChange: (value: T | "") => void;
}) {
  return (
    <div className="mt-10 flex flex-col gap-3">
      {options.map((option) => (
        <label
          key={option.value}
          className="flex items-center gap-3 text-xl text-black/80"
        >
          <input
            type="radio"
            checked={value === option.value}
            onChange={() => onChange(option.value)}
            className="h-5 w-5 accent-black"
          />
          {option.label}
        </label>
      ))}
      <label className="mt-2 flex items-center gap-3 text-base text-black/40">
        <input
          type="radio"
          checked={value === ""}
          onChange={() => onChange("")}
          className="h-5 w-5 accent-black"
        />
        응답하지 않음
      </label>
    </div>
  );
}

export default function OnboardingPage() {
  const router = useRouter();
  // 계정은 아직 없다 — 첫 화면에서 받은 이메일/비밀번호/이름(draft)에 이 화면의
  // 생년/고향/동의까지 다 모은 뒤, 마지막 단계에서 POST /users로 한 번에 생성한다.
  const [draft, setDraft] = useState<SignupDraft | null>(null);
  const [step, setStep] = useState(0);
  const [birthYear, setBirthYear] = useState("");
  const [hometown, setHometown] = useState("");
  const [educationLevel, setEducationLevel] = useState<EducationLevel | "">("");
  const [maritalStatus, setMaritalStatus] = useState<MaritalStatus | "">("");
  const [hasChildren, setHasChildren] = useState<"yes" | "no" | "">("");
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonMedicalDisclosure, setNonMedicalDisclosure] = useState<string | null>(null);

  useEffect(() => {
    // 3층 고지(기획안 4절: 비의료 서비스임을 온보딩에 명시) — 동의 화면에서 항상
    // 보여야 하므로 이 화면 진입 시점에 미리 받아둔다. 실패해도 온보딩 자체를
    // 막을 이유는 없어 조용히 무시한다(문구가 안 보이는 것 이상의 영향 없음).
    legalApi
      .getDisclosures()
      .then((d) => setNonMedicalDisclosure(d.non_medical_service))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const stored = signupDraft.get();
    if (stored) {
      setDraft(stored);
      return;
    }
    router.replace("/");
  }, [router]);

  if (!draft) return null;
  const displayName = draft.name;

  async function handleFinish() {
    if (!draft) return;
    setError(null);
    setSubmitting(true);
    try {
      const user = await usersApi.create({
        email: draft.email,
        password: draft.password,
        name: draft.name,
        birth_year: birthYear ? Number(birthYear) : undefined,
        hometown: hometown || undefined,
        education_level: educationLevel || undefined,
        marital_status: maritalStatus || undefined,
        has_children: hasChildren ? hasChildren === "yes" : undefined,
      });
      const { access_token, refresh_token } = await authApi.login({
        email: draft.email,
        password: draft.password,
      });
      session.setTokens(access_token, refresh_token);
      session.setUserId(user.id);

      await usersApi.createConsent(user.id, {
        consent_type: "data_collection",
        notice_version: CONSENT_NOTICE_VERSION,
        granted_by: "self",
      });

      signupDraft.clear();
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
              text={"최종 학력을 알려주세요."}
              className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
            />
            <p className="mt-3 text-sm text-black/40">
              답변에 맞는 이야기만 골라 물어보는 데 참고할게요. 답하고 싶지 않으면 넘어가셔도 돼요.
            </p>
            <RadioGroup options={EDUCATION_OPTIONS} value={educationLevel} onChange={setEducationLevel} />
          </>
        )}
        {step === 4 && (
          <>
            <Typewriter
              key="step4"
              text={"혼인 여부를 알려주세요."}
              className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
            />
            <RadioGroup options={MARITAL_OPTIONS} value={maritalStatus} onChange={setMaritalStatus} />
          </>
        )}
        {step === 5 && (
          <>
            <Typewriter
              key="step5"
              text={"자녀가 있으신가요?"}
              className="font-serif-kr text-2xl leading-relaxed text-black sm:text-3xl"
            />
            <RadioGroup options={CHILDREN_OPTIONS} value={hasChildren} onChange={setHasChildren} />
          </>
        )}
        {step === 6 && (
          <>
            <Typewriter
              key="step6"
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
            {nonMedicalDisclosure && (
              <p className="mt-6 max-w-md whitespace-pre-line text-sm leading-relaxed text-black/50">
                {nonMedicalDisclosure}
              </p>
            )}
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
            (step === 6 && !agreed) ||
            (step === 1 && birthYear !== "" && Number(birthYear) < 1900)
          }
        >
          {step < TOTAL_STEPS - 1 ? "다음" : submitting ? "시작하는 중..." : "동의하고 시작하기"}
        </Button>
      </div>
    </main>
  );
}
