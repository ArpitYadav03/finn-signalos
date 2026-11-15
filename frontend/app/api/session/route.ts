import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, SESSION_MAX_AGE, safeEqual, sessionToken } from "@/lib/session";

/** Is a workspace password configured? (Lets the sign-in screen say "open on this computer" honestly.) */
export async function GET() {
  return NextResponse.json({ password_required: !!process.env.FINN_SITE_PASSWORD });
}

/** Sign in with the shared workspace password. FINN has no individual accounts. */
export async function POST(req: NextRequest) {
  const pw = process.env.FINN_SITE_PASSWORD;
  let password = "";
  try { password = String((await req.json())?.password ?? ""); } catch { /* empty body */ }
  if (pw && !safeEqual(password, pw)) {
    await new Promise((r) => setTimeout(r, 400));   // slow down guessing
    return NextResponse.json({ detail: "That password isn't right." }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  if (pw) {
    res.cookies.set(SESSION_COOKIE, await sessionToken(pw), { httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production",
      path: "/", maxAge: SESSION_MAX_AGE });
  }
  return res;
}

export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, "", { path: "/", maxAge: 0 });
  return res;
}
