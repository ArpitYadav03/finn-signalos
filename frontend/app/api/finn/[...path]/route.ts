import { NextRequest } from "next/server";

/** Same-origin proxy to the FINN API. The workspace write key lives only in this server's environment
 *  (FINN_WORKSPACE_KEY, never NEXT_PUBLIC_*), so it is never sent to browsers; the API's CORS can stay narrow. */
const API = (process.env.FINN_API_URL || "http://localhost:8000").replace(/\/$/, "");
const KEY = process.env.FINN_WORKSPACE_KEY || "";

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (!path?.length || path[0] !== "v1" || path.some((p) => p === ".." || p.includes("\\"))) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  if (path[1] === "admin") return Response.json({ detail: "Admin endpoints are not proxied" }, { status: 403 });
  const url = `${API}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { "Content-Type": req.headers.get("content-type") || "application/json" };
  if (KEY && req.method !== "GET") headers["X-Workspace-Key"] = KEY;
  let res: Response;
  try {
    res = await fetch(url, { method: req.method, headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.text(), cache: "no-store" });
  } catch {
    return Response.json({ detail: "Can't reach the FINN backend." }, { status: 502 });
  }
  return new Response(res.body, { status: res.status, headers: { "Content-Type": res.headers.get("content-type") || "application/json" } });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
export const dynamic = "force-dynamic";
