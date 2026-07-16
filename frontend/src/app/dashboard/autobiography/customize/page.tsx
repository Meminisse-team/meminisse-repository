"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { StepperDots } from "@/components/ui/StepperDots";
import { autobiographiesApi } from "@/lib/api/autobiographies";
import { customizationApi } from "@/lib/api/customization";
import { useCurrentUser } from "@/lib/auth/useCurrentUser";
import type {
  Autobiography,
  CustomizationConfirmRequest,
  CustomizationOptionItem,
  CustomizationOptionsResponse,
  CustomizationRecommendationResponse,
  CustomizationState,
  SamplePreviewItem,
} from "@/types/api";

const POLL_INTERVAL_MS = 4000;
const MAX_PER_CATEGORY = 2;

type Category = "tones" | "structures" | "concepts";
const CATEGORIES: Category[] = ["tones", "structures", "concepts"];
const CATEGORY_LABEL: Record<Category, string> = { tones: "말투", structures: "구성", concepts: "컨셉" };

function readCustomization(autobiography: Autobiography): CustomizationState | null {
  const styleBible = autobiography.style_bible as { customization?: CustomizationState } | null;
  return styleBible?.customization ?? null;
}

/** 말투/구성/컨셉을 골라 8개 맛보기 샘플 중 하나로 자서전 전체 문체를 확정하는 화면.
 * consolidate(이야기 정리) 완료 후, 목차 만들기 이전에 들어가는 선택적 단계다 — 건너뛰고
 * 바로 목차를 만들어도 기본 문체로 동작하므로, 자서전 메인 화면에서 이 페이지로 오가는
 * 것을 자유롭게 허용한다(뒤로가기 링크 항상 노출).
 *
 * 옵션 선택(select) → 8개 샘플 생성 대기(previews, Celery 202라 폴링) → 확정(confirm)
 * 2단계로 진행하며, style_bible.customization에 이미 진행하던 기록이 있으면 그 지점부터
 * 이어서 보여준다(새로고침·재방문해도 처음부터 다시 고르지 않아도 되게). */
export default function CustomizePage() {
  const { user } = useCurrentUser();
  const router = useRouter();

  const [autobiography, setAutobiography] = useState<Autobiography | null>(null);
  const [options, setOptions] = useState<CustomizationOptionsResponse | null>(null);
  const [recommendations, setRecommendations] = useState<CustomizationRecommendationResponse | null>(null);
  const [selections, setSelections] = useState<Record<Category, string[]>>({
    tones: [],
    structures: [],
    concepts: [],
  });
  const [step, setStep] = useState<"select" | "previews">("select");
  const [previews, setPreviews] = useState<SamplePreviewItem[]>([]);
  const [confirmedSummary, setConfirmedSummary] = useState<CustomizationConfirmRequest | null>(null);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmingKey, setConfirmingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);
  const startPolling = useCallback((tick: () => void) => {
    if (!pollRef.current) pollRef.current = setInterval(tick, POLL_INTERVAL_MS);
  }, []);
  useEffect(() => stopPolling, [stopPolling]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;

    (async () => {
      try {
        const bio = await autobiographiesApi.get(user.id);
        const [opts, recs] = await Promise.all([
          customizationApi.getOptions(bio.id),
          customizationApi.getRecommendations(bio.id),
        ]);
        if (cancelled) return;

        setAutobiography(bio);
        setOptions(opts);
        setRecommendations(recs);

        const existing = readCustomization(bio);
        if (existing) {
          setSelections({
            tones: existing.tones,
            structures: existing.structures,
            concepts: existing.concepts,
          });
          if (existing.confirmed) {
            setConfirmedSummary(existing.confirmed);
          } else if (existing.tones.length > 0) {
            // select까지는 끝났지만 previews가 아직이면(생성 중이거나, 전에 들어왔다가
            // 나갔을 수 있음) 이어서 폴링으로 확인한다.
            setStep("previews");
            if (existing.previews && existing.previews.length > 0) {
              setPreviews(existing.previews);
            }
          }
        }
      } catch {
        if (!cancelled) setError("커스터마이징 정보를 불러오지 못했어요.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user]);

  // previews 폴링: previews 단계이고 아직 결과가 비어 있는 동안만.
  useEffect(() => {
    if (step !== "previews" || !autobiography || previews.length > 0) {
      stopPolling();
      return;
    }
    const tick = async () => {
      const result = await customizationApi.getPreviews(autobiography.id);
      if (result.samples.length > 0) {
        setPreviews(result.samples);
        stopPolling();
      }
    };
    void tick();
    startPolling(() => void tick());
    return () => stopPolling();
  }, [step, autobiography, previews.length, startPolling, stopPolling]);

  function toggleOption(category: Category, key: string) {
    setSelections((prev) => {
      const current = prev[category];
      if (current.includes(key)) {
        return { ...prev, [category]: current.filter((k) => k !== key) };
      }
      if (current.length >= MAX_PER_CATEGORY) return prev;
      return { ...prev, [category]: [...current, key] };
    });
  }

  async function handleSubmitSelection() {
    if (!autobiography) return;
    if (CATEGORIES.some((category) => selections[category].length === 0)) {
      setError("말투·구성·컨셉을 각 카테고리에서 최소 1개씩 골라주세요.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await customizationApi.select(autobiography.id, selections);
      setAutobiography(updated);
      setPreviews([]);
      await customizationApi.generatePreviews(autobiography.id);
      setStep("previews");
    } catch {
      setError("선택을 저장하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRetryPreviews() {
    if (!autobiography) return;
    setBusy(true);
    setError(null);
    try {
      await customizationApi.generatePreviews(autobiography.id);
    } catch {
      setError("맛보기 글을 다시 만들지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(sample: SamplePreviewItem) {
    if (!autobiography) return;
    const key = sampleKey(sample);
    setConfirmingKey(key);
    setError(null);
    try {
      await customizationApi.confirm(autobiography.id, {
        tone: sample.tone,
        structure: sample.structure,
        concept: sample.concept,
      });
      router.push("/dashboard/autobiography");
    } catch {
      setError("이 조합을 확정하지 못했어요. 잠시 후 다시 시도해주세요.");
      setConfirmingKey(null);
    }
  }

  if (loading) {
    return (
      <main className="px-6 pb-10 pt-14">
        <p className="text-black/50">불러오는 중...</p>
      </main>
    );
  }

  if (!autobiography || !options || !recommendations) {
    return (
      <main className="px-6 pb-10 pt-14">
        <p className="text-black/50">{error ?? "정보를 찾을 수 없어요."}</p>
      </main>
    );
  }

  return (
    <main className="px-6 pb-10 pt-14">
      <Link href="/dashboard/autobiography" className="mb-4 inline-block text-sm text-black/40">
        ← 자서전으로 돌아가기
      </Link>
      <h1 className="mb-2 font-serif-kr text-2xl text-black">말투와 분위기 정하기</h1>
      <p className="mb-8 text-base leading-relaxed text-black/50">
        내 자서전을 어떤 느낌으로 써 내려갈지 골라볼 수 있어요. 건너뛰어도 괜찮아요.
      </p>

      {error && <p className="mb-6 text-base text-black/60">{error}</p>}

      {confirmedSummary ? (
        <ConfirmedSummary
          options={options}
          confirmed={confirmedSummary}
          onRedo={() => setConfirmedSummary(null)}
        />
      ) : (
        <>
          <StepperDots steps={2} current={step === "select" ? 0 : 1} className="mb-8" />
          {step === "select" ? (
            <SelectStep
              options={options}
              recommendations={recommendations}
              selections={selections}
              busy={busy}
              onToggle={toggleOption}
              onSubmit={handleSubmitSelection}
            />
          ) : (
            <PreviewsStep
              previews={previews}
              confirmingKey={confirmingKey}
              busy={busy}
              onBack={() => setStep("select")}
              onRetry={handleRetryPreviews}
              onConfirm={handleConfirm}
            />
          )}
        </>
      )}
    </main>
  );
}

function sampleKey(sample: SamplePreviewItem): string {
  return `${sample.tone}-${sample.structure}-${sample.concept}`;
}

function RecommendationNote({ recommendations }: { recommendations: CustomizationRecommendationResponse }) {
  const hasAny =
    recommendations.tones.length + recommendations.structures.length + recommendations.concepts.length > 0;
  if (!hasAny) return null;

  return (
    <div className="mb-8 rounded-2xl border border-black/10 bg-black/[0.03] p-5">
      <p className="text-sm leading-relaxed text-black/60">
        {recommendations.source === "content_based"
          ? "지금까지 들려주신 이야기의 실제 내용을 바탕으로 어울리는 항목에 추천 배지를 붙였어요."
          : "지금까지 답변하신 질문들을 바탕으로 어울릴 만한 항목에 참고용 추천 배지를 붙였어요."}
      </p>
      {recommendations.reasoning && (
        <p className="mt-2 text-sm leading-relaxed text-black/50">“{recommendations.reasoning}”</p>
      )}
    </div>
  );
}

function SelectStep({
  options,
  recommendations,
  selections,
  busy,
  onToggle,
  onSubmit,
}: {
  options: CustomizationOptionsResponse;
  recommendations: CustomizationRecommendationResponse;
  selections: Record<Category, string[]>;
  busy: boolean;
  onToggle: (category: Category, key: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="flex flex-col gap-10">
      <RecommendationNote recommendations={recommendations} />

      {CATEGORIES.map((category) => (
        <OptionCategory
          key={category}
          label={CATEGORY_LABEL[category]}
          items={options[category]}
          recommendedKeys={recommendations[category]}
          selectedKeys={selections[category]}
          onToggle={(key) => onToggle(category, key)}
        />
      ))}

      <Button onClick={onSubmit} disabled={busy} className="w-full">
        {busy ? "저장하는 중..." : "이 조합으로 맛보기 글 만들어보기"}
      </Button>
    </div>
  );
}

function OptionCategory({
  label,
  items,
  recommendedKeys,
  selectedKeys,
  onToggle,
}: {
  label: string;
  items: CustomizationOptionItem[];
  recommendedKeys: string[];
  selectedKeys: string[];
  onToggle: (key: string) => void;
}) {
  return (
    <section>
      <h2 className="mb-1 text-lg font-medium text-black">{label}</h2>
      <p className="mb-4 text-sm text-black/40">최대 {MAX_PER_CATEGORY}개까지 고를 수 있어요.</p>
      <div className="flex flex-col gap-3">
        {items.map((item) => {
          const selected = selectedKeys.includes(item.key);
          const recommended = recommendedKeys.includes(item.key);
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onToggle(item.key)}
              aria-pressed={selected}
              className={`rounded-2xl border p-5 text-left transition-colors ${
                selected
                  ? "border-black bg-black text-white"
                  : "border-black/10 text-black hover:border-black/30"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base font-medium">{item.name}</span>
                {recommended && (
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      selected ? "bg-white text-black" : "bg-black text-white"
                    }`}
                  >
                    추천
                  </span>
                )}
              </div>
              <p className={`mt-1 text-sm leading-relaxed ${selected ? "text-white/70" : "text-black/50"}`}>
                {item.description}
              </p>
              {item.example && (
                <p
                  className={`mt-3 border-l-2 pl-3 font-serif-kr text-sm leading-relaxed ${
                    selected ? "border-white/30 text-white/60" : "border-black/15 text-black/45"
                  }`}
                >
                  {item.example}
                </p>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function PreviewsStep({
  previews,
  confirmingKey,
  busy,
  onBack,
  onRetry,
  onConfirm,
}: {
  previews: SamplePreviewItem[];
  confirmingKey: string | null;
  busy: boolean;
  onBack: () => void;
  onRetry: () => void;
  onConfirm: (sample: SamplePreviewItem) => void;
}) {
  if (previews.length === 0) {
    return (
      <div className="flex flex-col items-start gap-4 rounded-2xl border border-black/10 p-6">
        <p className="text-lg leading-relaxed text-black">고른 조합으로 맛보기 글을 쓰고 있어요...</p>
        <p className="text-sm text-black/40">
          몇 분 정도 걸릴 수 있어요. 이 화면을 열어두면 완료되는 대로 자동으로 나타나요.
        </p>
        <div className="flex gap-3">
          <Button variant="secondary" onClick={onBack}>
            선택 다시 하기
          </Button>
          <Button variant="secondary" onClick={onRetry} disabled={busy}>
            {busy ? "요청하는 중..." : "다시 요청하기"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <p className="text-lg leading-relaxed text-black">
          마음에 드는 글을 하나 골라주세요. 이 스타일로 자서전 전체를 써 내려가요.
        </p>
        <button type="button" onClick={onBack} className="shrink-0 text-sm text-black/40">
          선택 다시 하기
        </button>
      </div>

      {previews.map((sample) => {
        const key = sampleKey(sample);
        return (
          <div key={key} className="rounded-2xl border border-black/10 p-6">
            <div className="mb-3 flex flex-wrap gap-2">
              {[sample.tone_name, sample.structure_name, sample.concept_name].map((name) => (
                <span key={name} className="rounded-full bg-black/5 px-3 py-1 text-xs text-black/60">
                  {name}
                </span>
              ))}
            </div>
            <p className="whitespace-pre-wrap text-base leading-relaxed text-black/80">{sample.preview_text}</p>
            <Button
              variant="secondary"
              className="mt-5 w-full"
              disabled={confirmingKey !== null}
              onClick={() => onConfirm(sample)}
            >
              {confirmingKey === key ? "확정하는 중..." : "이 글로 확정하기"}
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function ConfirmedSummary({
  options,
  confirmed,
  onRedo,
}: {
  options: CustomizationOptionsResponse;
  confirmed: CustomizationConfirmRequest;
  onRedo: () => void;
}) {
  const toneName = options.tones.find((item) => item.key === confirmed.tone)?.name ?? confirmed.tone;
  const structureName =
    options.structures.find((item) => item.key === confirmed.structure)?.name ?? confirmed.structure;
  const conceptName = options.concepts.find((item) => item.key === confirmed.concept)?.name ?? confirmed.concept;

  return (
    <div className="flex flex-col items-start gap-6 rounded-2xl border border-black/10 p-6">
      <p className="text-lg leading-relaxed text-black">이미 자서전의 말투와 분위기를 정해두셨어요.</p>
      <div className="flex flex-wrap gap-2">
        {[toneName, structureName, conceptName].map((name) => (
          <span key={name} className="rounded-full bg-black/5 px-3 py-1 text-sm text-black/60">
            {name}
          </span>
        ))}
      </div>
      <div className="flex gap-3">
        <Link
          href="/dashboard/autobiography"
          className="rounded-lg bg-black px-6 py-3 text-lg font-medium text-white transition-colors hover:bg-black/80"
        >
          자서전으로 돌아가기
        </Link>
        <Button variant="secondary" onClick={onRedo}>
          다시 고르기
        </Button>
      </div>
    </div>
  );
}
