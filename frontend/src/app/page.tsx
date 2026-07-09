import { Button } from "@/components/ui/Button";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 px-6 text-center">
      <h1 className="text-4xl font-semibold tracking-tight">Meminisse</h1>
      <p className="max-w-md text-lg text-neutral-600 dark:text-neutral-400">
        부모님과의 대화를 한 권의 자서전으로. 뼈대 세팅 단계이며, 온보딩 화면은
        아직 설계 전입니다.
      </p>
      <Button disabled>시작하기 (준비 중)</Button>
    </main>
  );
}
