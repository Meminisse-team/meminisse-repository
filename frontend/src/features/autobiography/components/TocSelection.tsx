"use client";

import { Button } from "@/components/ui/Button";
import type { TocCandidate } from "@/types/api";

export function TocSelection({
  candidates,
  selecting,
  onSelect,
}: {
  candidates: TocCandidate[];
  selecting: number | null;
  onSelect: (index: number) => void;
}) {
  return (
    <div className="flex flex-col gap-6">
      <p className="text-lg leading-relaxed text-black">
        마음에 드는 목차를 하나 골라주세요. 이 목차를 바탕으로 각 장을 써 내려가요.
      </p>
      {candidates.map((candidate, index) => {
        const parts = candidate.parts ?? [];
        const hasParts = parts.length > 1;
        return (
          <div key={index} className="rounded-2xl border border-black/10 p-6">
            <p className="mb-4 text-sm text-black/40">목차 {index + 1}</p>
            {hasParts ? (
              <div className="flex flex-col gap-4">
                {parts.map((part) => (
                  <div key={part.part_index}>
                    <p className="mb-2 text-sm font-medium text-black/60">
                      {part.part_index}부. {part.part_title}
                    </p>
                    <ol className="flex flex-col gap-2">
                      {candidate.chapters
                        .filter((chapter) => chapter.part_index === part.part_index)
                        .map((chapter) => (
                          <li key={chapter.chapter_index} className="pl-2 text-base text-black">
                            {chapter.chapter_index}장. {chapter.title}
                          </li>
                        ))}
                    </ol>
                  </div>
                ))}
              </div>
            ) : (
              <ol className="flex flex-col gap-2">
                {candidate.chapters.map((chapter) => (
                  <li key={chapter.chapter_index} className="text-base text-black">
                    {chapter.chapter_index}장. {chapter.title}
                  </li>
                ))}
              </ol>
            )}
            <Button
              variant="secondary"
              className="mt-5 w-full"
              disabled={selecting !== null}
              onClick={() => onSelect(index)}
            >
              {selecting === index ? "선택하는 중..." : "이 목차로 진행"}
            </Button>
          </div>
        );
      })}
    </div>
  );
}
