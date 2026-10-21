"use client";

import { Button } from "@/components/ui/Button";
import { Spinner } from "./Spinner";

export function NeedsConsolidate({
  busy,
  triggered,
  onConsolidate,
}: {
  busy: boolean;
  triggered: boolean;
  onConsolidate: () => void;
}) {
  return (
    <div className="flex flex-col items-start gap-6 rounded-2xl border border-black/10 p-6">
      <p className="text-lg leading-relaxed text-black">
        지금까지 나눈 이야기를 하나로 모아 정리할게요. 정리가 끝나면 목차를 만들 수 있어요.
      </p>
      {triggered ? (
        <div className="flex items-center gap-2">
          <Spinner />
          <p className="animate-pulse text-sm text-black/40">
            이야기를 정리하고 있어요. 이 화면을 열어두면 자동으로 다음 화면(목차 만들기)으로
            넘어가요...
          </p>
        </div>
      ) : (
        <Button onClick={onConsolidate} disabled={busy}>
          {busy ? "시작하는 중..." : "이야기 정리하기"}
        </Button>
      )}
    </div>
  );
}
