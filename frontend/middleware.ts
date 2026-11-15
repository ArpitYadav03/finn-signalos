import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, safeEqual, sessionToken } from "@/lib/session";

/** Optional workspace password for a public demo (FINN_SITE_PASSWORD). FINN is a single shared workspace: everyone who
 *  signs in sees and edits the same portfolio, alerts and preferences. Unset = open (local development).
 *  Pages without a session go to the sign-in screen; API calls get 401. HTTP Basic auth still works for scripts. */
export async function middleware(req: NextRequest) {
  const pw = process.env.FINN_SITE_PASSWORD;
  if (!pw) return NextResponse.next();
  const { pathname, search } = req.nextUrl;
  if (pathname === "/login" || pathname === "/api/session" || pathname === "/icon.svg" || pathname === "/manifest.webmanifest") return NextResponse.next();
  const cookie = req.cookies.get(SESSION_COOKIE)?.value;
  if (cookie && safeEqual(cookie, await sessionToken(pw))) return NextResponse.next();
  const [scheme, value] = (req.headers.get("authorization") || "").split(" ");
  if (scheme === "Basic" && value) {
    try { if (safeEqual(atob(value).split(":").slice(1).join(":"), pw)) return NextResponse.next(); } catch { /* malformed header */ }
  }
  if (pathname.startsWith("/api/")) return NextResponse.json({ detail: "Sign in required" }, { status: 401 });
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
