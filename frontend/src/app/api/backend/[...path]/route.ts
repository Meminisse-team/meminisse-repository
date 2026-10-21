import { NextRequest, NextResponse } from "next/server";

const ACCESS = "meminisse_access";
const REFRESH = "meminisse_refresh";
const MAX_BODY = 11 * 1024 * 1024;
const cookieOptions = { httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "strict" as const, path: "/api/backend" };

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: Context) {
  const { path } = await context.params;
  if (path.some(p => !/^[a-zA-Z0-9_-]+$/.test(p)) || path[0] !== "api" || path[1] !== "v1") {
    return NextResponse.json({ detail: "Invalid API path" }, { status: 400 });
  }
  // Next may normalize loopback/internal URLs. APP_ORIGIN pins the public origin
  // behind a reverse proxy; otherwise compare with the actual Host, never a
  // client-supplied X-Forwarded-Host value.
  const expectedOrigin = process.env.APP_ORIGIN ?? `${request.nextUrl.protocol}//${request.headers.get("host") ?? request.nextUrl.host}`;
  if (!["GET", "HEAD"].includes(request.method) && request.headers.get("origin") !== expectedOrigin) {
    return NextResponse.json({ detail: "Cross-origin request denied" }, { status: 403 });
  }
  const endpoint = "/" + path.join("/");
  if (endpoint === "/api/v1/auth/logout") {
    if (request.method !== "POST") return new NextResponse(null, { status: 405 });
    const response = NextResponse.json({ ok: true });
    for (const name of [ACCESS, REFRESH]) response.cookies.set(name, "", { ...cookieOptions, maxAge: 0 });
    return response;
  }
  const isLogin = endpoint === "/api/v1/auth/login";
  const isRefresh = endpoint === "/api/v1/auth/refresh";
  if ((isLogin || isRefresh) && request.method !== "POST") return new NextResponse(null, { status: 405 });
  let body: Uint8Array | undefined;
  if (!["GET", "HEAD"].includes(request.method)) {
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    if (reader) {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BODY) { await reader.cancel(); return new NextResponse(null, { status: 413 }); }
        chunks.push(value);
      }
    }
    body = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
  }
  const headers = new Headers();
  if (request.headers.has("content-type")) headers.set("content-type", request.headers.get("content-type")!);
  const token = request.cookies.get(ACCESS)?.value;
  if (token && !isLogin && !isRefresh) headers.set("authorization", `Bearer ${token}`);
  if (isRefresh) {
    const refreshToken = request.cookies.get(REFRESH)?.value;
    if (!refreshToken) return new NextResponse(null, { status: 401 });
    headers.set("content-type", "application/json");
    body = new TextEncoder().encode(JSON.stringify({ refresh_token: refreshToken }));
  }
  try {
    const upstream = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}${endpoint}${request.nextUrl.search}`, {
      method: request.method, headers, body: body as BodyInit | undefined,
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(120_000),
    });
    if ((isLogin || isRefresh) && upstream.ok) {
      const tokens = await upstream.json();
      const response = NextResponse.json({ authenticated: true });
      response.cookies.set(ACCESS, tokens.access_token, { ...cookieOptions, maxAge: tokens.expires_in ?? 3600 });
      response.cookies.set(REFRESH, tokens.refresh_token, { ...cookieOptions, maxAge: 30 * 86400 });
      response.headers.set("Cache-Control", "no-store");
      return response;
    }
    const response = new NextResponse(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json", "Cache-Control": "no-store" },
    });
    if (isRefresh && [400, 401, 403].includes(upstream.status)) {
      for (const name of [ACCESS, REFRESH]) response.cookies.set(name, "", { ...cookieOptions, maxAge: 0 });
    }
    return response;
  } catch {
    return NextResponse.json({ detail: "API service unavailable" }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
