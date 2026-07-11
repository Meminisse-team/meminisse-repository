"use client";

import { useEffect, useState } from "react";

import { eventsApi } from "@/lib/api/events";
import type { EventItem } from "@/types/api";

export default function StoriesPage() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    eventsApi
      .list()
      .then(setEvents)
      .catch(() => setError("이야기를 불러오지 못했어요."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="px-6 pb-10 pt-14">
      <h1 className="mb-8 font-serif-kr text-2xl text-black">나의 이야기</h1>

      {loading && <p className="text-black/50">불러오는 중...</p>}
      {error && <p className="text-black/50">{error}</p>}
      {!loading && !error && events.length === 0 && (
        <p className="text-black/50">아직 나눈 이야기가 없어요. &apos;오늘의 대화&apos;에서 시작해보세요.</p>
      )}

      <div className="flex flex-col gap-5">
        {events.map((event) => (
          <article key={event.id} className="rounded-2xl border border-black/10 p-6">
            <p className="text-sm text-black/40">
              {new Date(event.created_at).toLocaleDateString("ko-KR")}
              {event.occurred_at_label ? ` · ${event.occurred_at_label}` : ""}
            </p>
            <h2 className="mt-2 text-lg font-semibold text-black">{event.one_line_summary}</h2>
            <p className="mt-3 text-base leading-relaxed text-black/70">{event.prose_paragraph}</p>
          </article>
        ))}
      </div>
    </main>
  );
}
