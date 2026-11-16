"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { safeNext } from "@/lib/session";

/** Sign-in screen. FINN has one shared workspace protected by an optional password (FINN_SITE_PASSWORD); there are no
 *  individual accounts yet, so Google / Apple sign-in are shown as not set up rather than faked. */
function Illustration() {
  return (
    <svg viewBox="0 0 320 200" className="mx-auto h-auto w-full max-w-[300px]" role="img" aria-label="A briefing card with a rising chart and a highlighted filing">
      <ellipse cx="160" cy="186" rx="118" ry="9" fill="var(--surface-3)" />
      <rect x="62" y="34" width="196" height="132" rx="18" fill="var(--surface)" stroke="var(--line)" />
      <rect x="80" y="52" width="70" height="8" rx="4" fill="var(--ink)" />
      <rect x="80" y="66" width="110" height="6" rx="3" fill="var(--line-strong)" />
      <path d="M82 140 L112 122 L138 130 L166 102 L194 110 L236 74" fill="none" stroke="var(--up)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="236" cy="74" r="6" fill="var(--up)" />
      <rect x="20" y="96" width="92" height="46" rx="12" fill="var(--surface)" stroke="var(--line)" />
      <circle cx="38" cy="119" r="8" fill="var(--accent)" />
      <rect x="52" y="112" width="48" height="6" rx="3" fill="var(--ink)" />
      <rect x="52" y="123" width="34" height="5" rx="2.5" fill="var(--line-strong)" />
      <rect x="222" y="18" width="80" height="40" rx="12" fill="var(--surface)" stroke="var(--line)" />
      <path d="M236 40 l6 6 l12 -12" fill="none" stroke="var(--up)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="262" y="34" width="28" height="6" rx="3" fill="var(--line-strong)" />
    </svg>
  );
}

function ProviderButton({ label, icon }: { label: string; icon: React.ReactNode }) {
  return (
    <button type="button" disabled aria-describedby="providers-note"
      className="flex min-h-[54px] w-full items-center justify-center gap-3 rounded-2xl border border-line bg-surface text-[15px] font-medium text-ink disabled:cursor-not-allowed">
      {icon}<span>{label}</span><span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-semibold text-ink-3">Not set up</span>
    </button>
  );
}

function LoginForm() {
  const sp = useSearchParams();
  const router = useRouter();
  const next = safeNext(sp.get("next"));
  const [required, setRequired] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    fetch("/api/session").then((r) => r.json()).then((d) => setRequired(!!d.password_required)).catch(() => setRequired(true));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/session", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ password }) });
      if (!r.ok) { setErr((await r.json().catch(() => ({})))?.detail || "Couldn't sign in. Try again."); input.current?.focus(); return; }
      router.replace(next);
    } catch { setErr("Couldn't reach FINN. Check your connection and try again."); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-40 overflow-y-auto bg-bg">
      <div className="mx-auto flex min-h-dvh max-w-[420px] flex-col px-6 pb-[calc(env(safe-area-inset-bottom)+24px)] pt-[calc(env(safe-area-inset-top)+40px)]">
        <div className="flex items-center justify-center gap-2.5">
          <span className="grid h-11 w-11 place-items-center rounded-2xl bg-cta text-[20px] font-black text-cta-ink" aria-hidden>F</span>
          <span className="text-[26px] font-bold tracking-tight">FINN</span>
        </div>
        <h1 className="mt-6 text-center text-[24px] font-bold tracking-tight">Sign in to your workspace</h1>
        <p className="mt-1 text-center text-[14px] text-ink-3">Signal first. Evidence always.</p>
        <div className="mt-6"><Illustration /></div>

        <div className="mt-8 space-y-3">
          <ProviderButton label="Continue with Google" icon={
            <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden><path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8Z"/><path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3c-1.1.7-2.5 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-5H1.3v3.1A12 12 0 0 0 12 24Z"/><path fill="#FBBC05" d="M5.3 14.3a7.2 7.2 0 0 1 0-4.6V6.6h-4a12 12 0 0 0 0 10.8l4-3.1Z"/><path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A11.6 11.6 0 0 0 12 0 12 12 0 0 0 1.3 6.6l4 3.1c.9-2.8 3.6-4.9 6.7-4.9Z"/></svg>} />
          <ProviderButton label="Continue with Apple" icon={
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor" aria-hidden><path d="M16.4 12.6c0-2.6 2.1-3.8 2.2-3.9-1.2-1.8-3.1-2-3.7-2-1.6-.2-3.1.9-3.9.9-.8 0-2-.9-3.4-.9-1.7 0-3.3 1-4.2 2.6-1.8 3.1-.5 7.7 1.3 10.2.9 1.2 1.9 2.6 3.2 2.6 1.3-.1 1.8-.8 3.3-.8 1.6 0 2 .8 3.4.8 1.4 0 2.3-1.3 3.1-2.5 1-1.4 1.4-2.8 1.4-2.9-.1 0-2.7-1-2.7-4.1ZM13.9 4.9c.7-.9 1.2-2 1-3.2-1 0-2.3.7-3 1.6-.7.8-1.3 2-1.1 3.1 1.2.1 2.3-.6 3.1-1.5Z"/></svg>} />
          <p id="providers-note" className="text-center text-[12px] text-ink-3">Google and Apple sign-in need to be registered for this deployment first.</p>
        </div>

        <div className="my-6 flex items-center gap-3 text-[12px] font-medium text-ink-3" aria-hidden><span className="h-px flex-1 bg-line" />OR<span className="h-px flex-1 bg-line" /></div>

        {required === false ? (
          <div className="space-y-3 text-center">
            <p className="text-[13.5px] text-ink-2">Demo access: no password needed.</p>
            <button className="btn btn-primary min-h-[54px] w-full text-[15px]" onClick={submit} disabled={busy} aria-busy={busy} data-testid="continue-guest">
              {busy ? "Opening FINN…" : "Continue to FINN"}</button>
            {err && <p role="alert" className="text-[13px] text-down">{err}</p>}
          </div>
        ) : (
          <form onSubmit={submit} noValidate>
            <label htmlFor="pw" className="sr-only">Workspace password</label>
            <div className={`flex items-center gap-2 rounded-2xl border bg-surface py-1.5 pl-4 pr-1.5 ${err ? "border-down" : "border-line"} focus-within:border-ink`}>
              <input ref={input} id="pw" type="password" autoComplete="current-password" enterKeyHint="go" value={password}
                onChange={(e) => { setPassword(e.target.value); setErr(null); }} placeholder="Continue with workspace password"
                aria-invalid={!!err} aria-describedby={err ? "pw-err" : undefined} disabled={required === null}
                className="min-w-0 flex-1 bg-transparent py-2.5 text-[15px] outline-none placeholder:text-ink-3" />
              <button type="submit" aria-label="Sign in" disabled={busy || !password}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-cta text-cta-ink transition-transform active:scale-95 disabled:opacity-40">
                {busy ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-cta-ink border-t-transparent" aria-hidden />
                  : <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M5 12h14m-6-6 6 6-6 6" /></svg>}
              </button>
            </div>
            {err && <p id="pw-err" role="alert" className="mt-2 text-[13px] text-down">{err}</p>}
          </form>
        )}

        <p className="mt-auto pt-10 text-center text-[12px] leading-relaxed text-ink-3">
          FINN is a research aid, not investment advice. Everyone signed in to this workspace shares the same portfolio, plans and alerts.
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense fallback={null}><LoginForm /></Suspense>;
}
