import { clearLegacySession } from "@/lib/auth/session";

const API_BASE_URL = "/api/backend";
let refreshGeneration = 0;
let refreshPromise: Promise<boolean> | null = null;
async function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch(`${API_BASE_URL}/api/v1/auth/refresh`, { method: "POST", credentials: "same-origin" })
      .then(response => { if (response.ok) refreshGeneration += 1; return response.ok; }).catch(() => false).finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  clearLegacySession();
  const requestGeneration = refreshGeneration;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });

  if (response.status === 401 && !retried && !path.startsWith("/api/v1/auth/login") && !path.startsWith("/api/v1/auth/refresh")) {
    if (requestGeneration !== refreshGeneration || await refreshSession()) return request<T>(path, init, true);
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(response.status, detail || response.statusText);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body ?? {}),
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: JSON.stringify(body ?? {}),
    }),
};
