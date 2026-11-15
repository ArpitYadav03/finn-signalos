"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/", label: "Briefing", icon: "M4 6h16M4 12h10M4 18h6" },
  { href: "/portfolio", label: "Portfolio", icon: "M4 19V9m6 10V5m6 14v-7m4 7H2" },
  { href: "/research", label: "Research", icon: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm9 16-4.3-4.3" },
  { href: "/alerts", label: "Alerts", icon: "M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8Zm4 12h4" },
];

function Icon({ d, active }: { d: string; active?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="h-[22px] w-[22px]" fill="none" stroke="currentColor" strokeWidth={active ? 2.2 : 1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-cta text-[15px] font-black text-cta-ink shadow-[0_6px_14px_-8px_var(--cta)]">F<span className="sr-only">INN</span></span>
      {!compact && (
        <span className="leading-tight">
          <span className="block text-[15px] font-bold tracking-tight">FINN</span>
          <span className="block text-[11px] text-ink-3">Signal first. Evidence always.</span>
        </span>
      )}
    </div>
  );
}

export function Nav() {
  const path = usePathname();
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  if (path.startsWith("/login")) return null;   // the sign-in screen is full-bleed
  const signOut = async () => { await fetch("/api/session", { method: "DELETE" }).catch(() => undefined); window.location.href = "/login"; };
  return (
    <>
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-8 px-4 py-8 md:flex">
        <div className="px-2"><Brand /></div>
        <nav className="flex flex-col gap-1" aria-label="Primary">
          {items.map((i) => {
            const on = active(i.href);
            return (
              <Link key={i.href} href={i.href} aria-current={on ? "page" : undefined}
                className={`relative flex min-h-[44px] items-center gap-3 rounded-xl px-3 text-[14px] font-medium transition-colors ${on ? "bg-cta-soft text-ink" : "text-ink-2 hover:bg-surface hover:text-ink"}`}>
                {on && <span className="absolute left-0 top-2.5 h-6 w-[3px] rounded-full bg-cta" aria-hidden />}
                <Icon d={i.icon} active={on} /> {i.label}
              </Link>
            );
          })}
        </nav>
        <button onClick={signOut} className="mt-auto flex min-h-[40px] items-center gap-3 rounded-xl px-3 text-[13.5px] font-medium text-ink-2 hover:bg-surface hover:text-ink">
          <Icon d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H3" /> Sign out</button>
        <p className="px-2 text-[11px] leading-relaxed text-ink-3">Research aid, not investment advice. Answers cite company filings, annual reports and call transcripts only.</p>
      </aside>
      {/* Floating tab bar (iOS / Uber style): raised, blurred, with a pill behind the active tab. */}
      <nav className="bottom-nav fixed inset-x-0 bottom-0 z-30 px-3 pb-[calc(env(safe-area-inset-bottom)+10px)] md:hidden" aria-label="Primary">
        <div className="mx-auto grid max-w-md grid-cols-4 gap-1 rounded-[22px] border border-line bg-surface/85 p-1.5 shadow-[var(--shadow-float)] backdrop-blur-xl">
          {items.map((i) => {
            const on = active(i.href);
            return (
              <Link key={i.href} href={i.href} aria-current={on ? "page" : undefined}
                className={`flex min-h-[52px] flex-col items-center justify-center gap-0.5 rounded-2xl text-[11px] font-semibold transition-colors ${on ? "bg-cta-soft text-ink" : "text-ink-3 hover:text-ink-2"}`}>
                <span className={on ? "text-cta" : ""}><Icon d={i.icon} active={on} /></span>
                {i.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </>
  );
}
