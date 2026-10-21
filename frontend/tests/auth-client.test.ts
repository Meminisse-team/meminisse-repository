import { describe, it, expect, vi, afterEach } from "vitest";
import { apiClient, ApiError } from "@/lib/api/client";
afterEach(() => vi.unstubAllGlobals());
describe("cookie session API client", () => {
  it("coalesces concurrent 401s into one refresh and retries original bodies once", async () => {
    let refreshed = false;
    let completeRefresh!: () => void;
    const gate = new Promise<void>(resolve => { completeRefresh = resolve; });
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/auth/refresh")) { await gate; refreshed = true; return Response.json({ authenticated: true }); }
      expect(new Headers(init?.headers).has("Authorization")).toBe(false);
      return refreshed ? Response.json({ ok: true }) : new Response(null, { status: 401 });
    });
    vi.stubGlobal("fetch", fetcher);
    localStorage.setItem("meminisse.refresh_token", "legacy");
    const first = apiClient.post("/api/v1/example", { content: "hello" });
    const second = apiClient.get("/api/v1/another");
    await vi.waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/auth/refresh"))).toHaveLength(1));
    completeRefresh();
    await expect(Promise.all([first, second])).resolves.toEqual([{ ok: true }, { ok: true }]);
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/auth/refresh"))).toHaveLength(1);
    const posts = fetcher.mock.calls.filter(([url]) => url.endsWith("/example"));
    expect(posts.map(([, init]) => init?.body)).toEqual(['{"content":"hello"}', '{"content":"hello"}']);
    expect(localStorage.getItem("meminisse.refresh_token")).toBeNull();
  });
  it("never loops after a rejected refresh or refresh-success/retry-401", async () => {
    const fetcher = vi.fn(async (url: string) => url.endsWith("/auth/refresh") ? Response.json({ authenticated: true }) : new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(apiClient.get("/api/v1/private")).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledTimes(3);
    fetcher.mockClear().mockImplementation(async () => new Response(null, { status: 401 }));
    await expect(apiClient.get("/api/v1/private")).rejects.toMatchObject({ status: 401 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("does not refresh a failed login", async () => {
    const fetcher = vi.fn(async () => new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(apiClient.post("/api/v1/auth/login", {})).rejects.toMatchObject({ status: 401 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
it("reuses a completed refresh for a late 401 from the same request generation", async () => {
  let refreshed = false;
  let releaseLate!: () => void;
  const gate = new Promise<void>(resolve => { releaseLate = resolve; });
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("/auth/refresh")) { refreshed = true; return Response.json({ authenticated: true }); }
    if (url.endsWith("/late") && !refreshed) { await gate; return new Response(null, { status: 401 }); }
    return refreshed ? Response.json({ ok: true }) : new Response(null, { status: 401 });
  });
  vi.stubGlobal("fetch", fetcher);
  const late = apiClient.get("/api/v1/late");
  await apiClient.get("/api/v1/first");
  releaseLate();
  await expect(late).resolves.toEqual({ ok: true });
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/auth/refresh"))).toHaveLength(1);
});
