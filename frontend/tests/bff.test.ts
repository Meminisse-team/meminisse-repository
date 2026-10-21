// @vitest-environment node
import { it, expect, vi, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { POST, GET } from "@/app/api/backend/[...path]/route";
const ctx = (endpoint: string) => ({ params: Promise.resolve(["api", "v1", "auth", endpoint]).then(path => ({ path })) });
const request = (endpoint: string, options: RequestInit = {}) => new NextRequest(`https://meminisse.test/api/backend/api/v1/auth/${endpoint}`, { ...options, signal: options.signal ?? undefined });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
it("keeps both login tokens in HttpOnly cookies and out of JSON", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ access_token: "access-secret", refresh_token: "refresh-secret", expires_in: 300 })));
  const response = await POST(request("login", { method: "POST", headers: { origin: "https://meminisse.test", "content-type": "application/json" }, body: '{"email":"test","password":"secret"}' }), ctx("login"));
  expect(await response.json()).toEqual({ authenticated: true });
  expect(response.cookies.get("meminisse_access")?.value).toBe("access-secret");
  expect(response.cookies.get("meminisse_refresh")?.value).toBe("refresh-secret");
  expect(response.headers.get("set-cookie")).toMatch(/HttpOnly/i);
  expect(response.headers.get("set-cookie")).toMatch(/SameSite=strict/i);
});
it("rejects cross-origin mutation and safe-method logout", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const response = await POST(request("login", { method: "POST", headers: { origin: "https://attacker.test" } }), ctx("login"));
  expect(response.status).toBe(403);
  expect((await GET(request("logout"), ctx("logout"))).status).toBe(405);
  expect(fetcher).not.toHaveBeenCalled();
});
it("refreshes from the cookie only, then clears invalid credentials", async () => {
  const fetcher = vi.fn(async (_url, init) => {
    expect(JSON.parse(init.body instanceof Uint8Array ? new TextDecoder().decode(init.body) : init.body)).toEqual({ refresh_token: "cookie-secret" });
    return new Response(null, { status: 401 });
  }); vi.stubGlobal("fetch", fetcher);
  const response = await POST(request("refresh", { method: "POST", headers: { origin: "https://meminisse.test", cookie: "meminisse_refresh=cookie-secret" }, body: '{"refresh_token":"injected"}' }), ctx("refresh"));
  expect(response.status).toBe(401);
  expect(response.cookies.get("meminisse_refresh")?.value).toBe("");
});
it("sets Secure cookies in production", async () => {
  vi.stubEnv("NODE_ENV", "production");
  vi.resetModules();
  const { POST: productionPost } = await import("@/app/api/backend/[...path]/route");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ access_token: "access", refresh_token: "refresh" })));
  const response = await productionPost(request("login", { method: "POST", headers: { origin: "https://meminisse.test" }, body: "{}" }), ctx("login"));
  expect(response.headers.get("set-cookie")).toMatch(/Secure/);
});
it("uses the actual Host when Next normalizes an internal URL", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 422 })));
  const response = await POST(new NextRequest("http://localhost:3001/api/backend/api/v1/auth/login", { method: "POST", headers: { host: "127.0.0.1:3001", origin: "http://127.0.0.1:3001" }, body: "{}" }), ctx("login"));
  expect(response.status).toBe(422);
});
it("pins the deployment origin and rejects spoofed forwarding headers", async () => {
  vi.stubEnv("APP_ORIGIN", "https://meminisse.test");
  const fetcher = vi.fn(async () => new Response(null, { status: 422 })); vi.stubGlobal("fetch", fetcher);
  const response = await POST(request("login", { method: "POST", headers: { origin: "https://attacker.test", "x-forwarded-host": "attacker.test" }, body: "{}" }), ctx("login"));
  expect(response.status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});
