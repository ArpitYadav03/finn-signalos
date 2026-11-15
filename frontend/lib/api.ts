// Default: the web app's own server-side proxy (/api/finn → FINN_API_URL), which adds the workspace key for writes.
// NEXT_PUBLIC_API_URL can point the browser straight at a local API during development (writes then need localhost).
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "/api/finn").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${API_BASE.endsWith("/api/finn") ? path.replace(/^\/api/, "") : path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Can't reach the FINN backend. Is it running?");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : Array.isArray(body?.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

export const docFileUrl = (id: number, page?: number | null) =>
  `${API_BASE}${API_BASE.endsWith("/api/finn") ? "" : "/api"}/v1/documents/${id}/file${page ? `#page=${page}` : ""}`;
