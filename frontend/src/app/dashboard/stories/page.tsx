import { dummyStories } from "@/lib/dummy/stories";

export default function StoriesPage() {
  return (
    <main className="px-6 pb-10 pt-14">
      <h1 className="mb-8 font-serif-kr text-2xl text-black">나의 이야기</h1>

      <div className="flex flex-col gap-5">
        {dummyStories.map((story) => (
          <article key={story.id} className="rounded-2xl border border-black/10 p-6">
            <p className="text-sm text-black/40">{story.date}</p>
            <h2 className="mt-2 text-lg font-semibold text-black">{story.summary}</h2>
            <p className="mt-3 text-base leading-relaxed text-black/70">{story.prose}</p>
          </article>
        ))}
      </div>
    </main>
  );
}
