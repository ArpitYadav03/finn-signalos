"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Briefing } from "@/lib/types";
import { ago, fmtDay, fmtPct, fmtPrice, fmtTime, tone, windowLabel } from "@/lib/format";
import { Brand } from "@/components/Nav";
import { ReviewFeed } from "@/components/ReviewFeed";
import { HandoverBriefing } from "@/components/Handover";
import { dedupeAcross, parseView, viewHref, VIEWS, type View } from "@/lib/briefingView";
import { useRouter } from "next/navigation";
import { RoutineRow, SignalCard } from "@/components/SignalCard";
import { ErrorState, Skeleton } from "@/components/ui";

const PRESETS = [
  { h: 24, label: "24h" },
  { h: 72, label: "3d" },
  { h: 168, label: "7d" },
  { h: 720, label: "30d" },
];

type Win = { kind: "preset"; hours: number } | { kind: "custom"; since: string; until: string };

// datetime-local value (local time, no zone) -> ISO with the browser's offset
const toIso = (local: string) => new Date(local).toISOString();
const localNow = (offsetH = 0) => {
  const d = new Date(Date.now() - offsetH * 3600e3);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};

interface FilingsSource { limited_window: boolean; complete_for_window: boolean; complete_from: string | null; checked_through: string | null;
  gaps: { ticker: string; covered_from: string | null; last_success: string | null }[]; never_checked: string[];
  failing: { ticker: string; last_success: string | null; error: string }[] }

/** The filings source lists only each company's latest filings: say exactly which part of the window is covered. */
function WindowCoverage({ b }: { b: Briefing }) {
  const fs = (b.coverage as unknown as { filings_source?: FilingsSource }).filings_source;
  const [open, setOpen] = useState(false);
  if (!fs) return null;
  if (fs.complete_for_window) {
    return <p className="text-[12.5px] text-ink-3">Every holding&apos;s filings are covered for this whole window (continuous since {fmtTime(fs.complete_from)}).</p>;
  }
  const n = fs.gaps.length + fs.never_checked.length;
  return (
    <div className="rounded-xl border border-line bg-surface-2/50 px-3 py-2 text-[12.5px] text-ink-2" data-testid="window-coverage">
      <span className="font-medium text-ink">Partial coverage for this window.</span> FINN&apos;s filing source lists only the latest
      filings per company, so {n} holding{n === 1 ? "" : "s"} may have filings here that FINN never saw.
      {fs.complete_from && <> Coverage is complete for all holdings from {fmtTime(fs.complete_from)}.</>}
      <button className="ml-1 text-info" onClick={() => setOpen((v) => !v)} aria-expanded={open}>{open ? "Hide" : "Show"} gaps</button>
      {open && (
        <ul className="mt-1.5 space-y-0.5 text-[12px] text-ink-3">
          {fs.gaps.map((g) => <li key={g.ticker}>{g.ticker}: covered from {g.covered_from ? fmtTime(g.covered_from) : "unknown"} · last checked {fmtTime(g.last_success)}</li>)}
          {fs.never_checked.map((t) => <li key={t}>{t}: not checked yet since coverage tracking began</li>)}
          {fs.failing.map((f) => <li key={f.ticker}>{f.ticker}: last check failed ({f.error}); last success {fmtTime(f.last_success)}</li>)}
        </ul>
      )}
    </div>
  );
}

function CollectionNote({ b }: { b: Briefing }) {
  const c = b.collection;
  const missing = [...(c.filings_unavailable_for || [])];
  const noPrices = c.prices_unavailable_for || [];
  if (c.status === "never") return <p className="text-[12.5px] text-ink-3">No data refresh has run yet.</p>;
  if (missing.length || noPrices.length || c.status !== "ok") {
    return (
      <div role="status" className="rounded-xl border border-accent/40 bg-accent/5 px-3 py-2 text-[12.5px] text-ink-2">
        <span className="font-medium text-ink">Some sources were unavailable</span> in the last refresh ({fmtTime(c.finished_at || c.started_at)}).
        {missing.length > 0 && <> Filings could not be checked for {missing.join(", ")}.</>}
        {noPrices.length > 0 && <> No prices for {noPrices.join(", ")}.</>} An empty list may be incomplete for these companies.
      </div>
    );
  }
  // The last *complete* refresh of every holding, not the latest single ingestion (which can be one company's filing).
  return <p className="text-[12.5px] text-ink-3">Last complete refresh of all {b.coverage.holdings} holdings: {fmtTime(c.finished_at)} ({ago(c.finished_at)}).</p>;
}

export default function BriefingPage() {
  const [win, setWin] = useState<Win>({ kind: "preset", hours: 24 });
  const [customOpen, setCustomOpen] = useState(false);
  const [from, setFrom] = useState(localNow(48));
  const [to, setTo] = useState(localNow(0));
  const [data, setData] = useState<Briefing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showRoutine, setShowRoutine] = useState(false);
  const [today, setToday] = useState("");
  const [planParam, setPlanParam] = useState<number | null>(null);
  const [view, setView] = useState<View | null>(null);
  const router = useRouter();
  useEffect(() => {
    try {
      const v = Number(new URLSearchParams(window.location.search).get("plan")); if (v) setPlanParam(v);
      setView(parseView(window.location.search));
    } catch { setView("today"); }
  }, []);
  // Switching views only changes the URL (shareable, Back works); it never marks anything reviewed.
  const [counts, setCounts] = useState<{ plans: number | null; unreviewed: number | null }>({ plans: null, unreviewed: null });
  useEffect(() => {   // read-only counts for the tiles; nothing is marked reviewed by fetching them
    api<{ enabled: boolean }[]>("/api/v1/plans").then((p) => setCounts((c) => ({ ...c, plans: p.filter((x) => x.enabled).length }))).catch(() => undefined);
    api<{ new: { event_key?: string; id: number }[]; updated: { event_key?: string; id: number }[]; unresolved: { event_key?: string; id: number }[] }>("/api/v1/review")
      .then((f) => setCounts((c) => ({ ...c, unreviewed: dedupeAcross([f.updated, f.new, f.unresolved], (x) => x.event_key || String(x.id)).flat().length })))
      .catch(() => undefined);
  }, []);
  const [greeting, setGreeting] = useState("Briefing");
  const [search, setSearch] = useState("");
  useEffect(() => {
    const h = Number(new Date().toLocaleString("en-IN", { hour: "numeric", hour12: false, timeZone: "Asia/Kolkata" }));
    setGreeting(h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening");
  }, []);
  const pick = (v: View) => { setView(v); router.replace(viewHref(window.location.search, v), { scroll: false }); };
  useEffect(() => setToday(new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "Asia/Kolkata" })), []);

  const load = useCallback(() => {
    setErr(null);
    setData(null);
    const qs = win.kind === "preset" ? `hours=${win.hours}` : `since=${encodeURIComponent(toIso(win.since))}&until=${encodeURIComponent(toIso(win.until))}`;
    api<Briefing>(`/api/v1/signals?${qs}`).then(setData).catch((e) => setErr(e.message));
  }, [win]);
  useEffect(load, [load]);

  const c = data?.counts;
  const n = c ? c.high + c.medium : 0;
  const label = data ? windowLabel(data.window.hours, !!data.window.custom, data.window.start, data.window.end) : "";
  const widen = win.kind === "preset" && win.hours < 168 ? 168 : win.kind === "preset" && win.hours < 720 ? 720 : null;

  return (
    <div>
      <header className="flex items-center justify-between md:hidden"><Brand /></header>
      <div className="mt-5 flex items-baseline justify-between gap-3 md:mt-0 lg:max-w-[calc(100%-344px)]">
        <h1 className="text-[24px] font-bold leading-tight tracking-tight md:text-[28px]">{greeting}</h1>
        <p className="shrink-0 text-[12.5px] font-medium text-ink-3">{today}</p>
      </div>
      {/* Summary tiles double as the view switcher (Robinhood / Revolut pattern): the number is the reason to tap. */}
      <div className="mt-4 grid grid-cols-3 gap-2 lg:max-w-[calc(100%-344px)]" role="tablist" aria-label="Briefing views">
        {VIEWS.map((v) => {
          const n = v.id === "today" ? (data ? (data.counts.high + data.counts.medium) : null) : v.id === "plans" ? counts.plans : counts.unreviewed;
          const sub = v.id === "today" ? (data ? (win.kind === "preset" && win.hours === 24 ? "last 24 h" : "this window") : "loading") : v.id === "plans" ? "active plans" : "to review";
          return (
            <button key={v.id} role="tab" id={`tab-${v.id}`} aria-selected={view === v.id} aria-controls="briefing-view" title={v.hint} onClick={() => pick(v.id)}
              className="stat min-h-[84px]" data-testid={`view-${v.id}`}>
              <span className="block text-[12.5px] font-semibold text-ink-2">{v.label}</span>
              <span className="num mt-1 block text-[24px] font-semibold leading-none text-ink">{n ?? "–"}</span>
              <span className="mt-1 block text-[11.5px] text-ink-3">{sub}</span>
            </button>
          );
        })}
      </div>
      {/* Research any listed company by NSE symbol (symbols only: there is no company-name search). */}
      <form role="search" className="mt-3 flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-4 pr-1 shadow-[var(--shadow)] lg:max-w-[calc(100%-344px)]"
        onSubmit={(e) => { e.preventDefault(); const t = search.trim().toUpperCase(); if (t) router.push(`/research?ticker=${encodeURIComponent(t)}`); }}>
        <svg viewBox="0 0 24 24" className="h-5 w-5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden><path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm9 16-4.3-4.3" /></svg>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search an NSE symbol, e.g. INFY" aria-label="NSE symbol to research" autoCapitalize="characters" autoCorrect="off" spellCheck={false}
          className="min-w-0 flex-1 bg-transparent py-2 text-[15px] outline-none placeholder:text-ink-3" />
        <button className="btn btn-primary min-h-[40px] px-4 text-[13.5px]" disabled={!search.trim()}>Go</button>
      </form>
      {data && view !== "today" && <div className="mt-3 lg:max-w-[calc(100%-344px)]"><CollectionNote b={data} /></div>}
      <div id="briefing-view" role="tabpanel" aria-labelledby={view ? `tab-${view}` : undefined}>
      {view === "plans" && <div className="mt-4 lg:max-w-[calc(100%-344px)]"><HandoverBriefing planId={planParam} /></div>}
      {view === "unreviewed" && <div className="mt-4 lg:max-w-[calc(100%-344px)]"><ReviewFeed /></div>}
      {view === "today" && (<>
      <div className="mt-4">
        <h2 className="text-[22px] font-bold leading-tight tracking-tight" data-testid="briefing-summary">
          {!data ? "Digest by time window" : n === 0 ? "Nothing material in this window" : `${n} development${n === 1 ? "" : "s"} in this window`}
        </h2>
        {data && <p className="mt-1 text-[14px] text-ink-2">Across {data.coverage.holdings} holdings · {label}</p>}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-1.5" role="group" aria-label="Time window">
        {PRESETS.map((w) => {
          const on = win.kind === "preset" && win.hours === w.h;
          return (
            <button key={w.h} aria-pressed={on} onClick={() => { setWin({ kind: "preset", hours: w.h }); setCustomOpen(false); }}
              aria-label={`Show the last ${w.label}`} className="chip">{w.label}</button>
          );
        })}
        <button aria-pressed={win.kind === "custom"} aria-expanded={customOpen} onClick={() => setCustomOpen((v) => !v)}
          className="chip">Custom</button>
      </div>
      {customOpen && (
        <form className="card mt-3 grid gap-3 p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); if (from < to) setWin({ kind: "custom", since: from, until: to }); }}>
          <label className="text-[12.5px] text-ink-3">From<input type="datetime-local" value={from} max={to} onChange={(e) => setFrom(e.target.value)}
            className="num mt-1 block w-full rounded-lg border border-line bg-bg px-2 py-2 text-[13.5px] text-ink" /></label>
          <label className="text-[12.5px] text-ink-3">To<input type="datetime-local" value={to} min={from} onChange={(e) => setTo(e.target.value)}
            className="num mt-1 block w-full rounded-lg border border-line bg-bg px-2 py-2 text-[13.5px] text-ink" /></label>
          <button className="btn btn-primary" disabled={!(from < to)}>Apply</button>
          {!(from < to) && <p className="text-[12px] text-down sm:col-span-3">“From” must be before “To”.</p>}
        </form>
      )}

      {err && <div className="mt-6"><ErrorState message={err} retry={load} /></div>}
      {!data && !err && <div className="mt-6 space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-44" />)}</div>}

      {data && (
        <div className="mt-5 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 space-y-4">
            <CollectionNote b={data} />
            <WindowCoverage b={data} />
            {data.alerts.length > 0 && (
              <Link href="/alerts" className="block rounded-xl border border-accent/40 bg-accent/5 p-3">
                <p className="label text-accent">Triggered alerts</p>
                <ul className="mt-1 space-y-0.5 text-[13.5px] text-ink-2">{data.alerts.map((a) => <li key={a.id}>{a.message}</li>)}</ul>
              </Link>
            )}
            {data.signals.length === 0 ? (
              <div className="card px-5 py-6 text-center" data-testid="quiet-window">
                <p className="mx-auto max-w-md text-[13px] leading-relaxed text-ink-3">
                  {data.coverage.filings === 0 ? "FINN found no filings from your holdings in this window." :
                    `${data.coverage.filings} filing${data.coverage.filings === 1 ? " was" : "s were"} published; none rose above routine.`}
                  {data.collection.complete ? " The last refresh completed for every holding." : ""}
                </p>
                {widen && <button className="btn btn-primary mt-4" onClick={() => setWin({ kind: "preset", hours: widen })}>Show the last {widen / 24} days</button>}
              </div>
            ) : (
              <div className="space-y-3">{data.signals.map((s) => <SignalCard key={s.id} s={s} />)}</div>
            )}
          </div>

          <aside className="min-w-0 space-y-4" aria-label="Context">
            {data.anomalies.length > 0 && (
              <section className="card p-4">
                <p className="label">Unusual moves · {fmtDay(data.anomalies[0].trading_date)} close</p>
                <ul className="mt-2 divide-y divide-line">
                  {data.anomalies.map((a) => (
                    <li key={a.ticker}>
                      <Link href={`/research?ticker=${a.ticker}`} className="flex items-center justify-between gap-3 py-2">
                        <span className="text-[13.5px] font-semibold">{a.ticker}</span>
                        <span className="text-right">
                          <span className={`num block text-[14px] ${tone(a.return_pct)}`}>{fmtPct(a.return_pct)}</span>
                          <span className="num block text-[11.5px] text-ink-3">{fmtPrice(a.close)}{a.volume_ratio !== null ? ` · ${a.volume_ratio.toFixed(1)}× avg vol` : ""}</span>
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <section className="card">
              <button className="flex min-h-[48px] w-full items-center justify-between px-4 text-left" onClick={() => setShowRoutine((v) => !v)} aria-expanded={showRoutine}>
                <span className="text-[13.5px] text-ink-2">{data.routine.length} routine filing{data.routine.length === 1 ? "" : "s"}</span>
                <span className="text-[12.5px] text-ink-3">{showRoutine ? "Hide" : "Show"}</span>
              </button>
              {showRoutine && (data.routine.length ? <ul className="divide-y divide-line border-t border-line px-4">{data.routine.map((s) => <RoutineRow key={s.id} s={s} />)}</ul>
                : <p className="border-t border-line px-4 py-3 text-[12.5px] text-ink-3">No routine filings in this window.</p>)}
            </section>
            <section className="card p-4 text-[12.5px] text-ink-3">
              <p className="label">Data freshness</p>
              <dl className="mt-2 grid grid-cols-2 gap-2">
                <div><dt>Closing prices</dt><dd className="text-ink">{fmtDay(data.freshness.latest_trading_date)} session</dd></div>
                <div><dt>Last complete refresh</dt><dd className="text-ink">{data.collection.complete ? fmtTime(data.collection.finished_at) : `incomplete (${fmtTime(data.collection.finished_at || data.collection.started_at)})`}</dd></div>
                <div><dt>Filings covered through</dt><dd className="text-ink">{fmtTime((data.coverage as unknown as { filings_source?: FilingsSource }).filings_source?.checked_through ?? null)}</dd></div>
                <div><dt>Latest single update</dt><dd className="text-ink">{fmtTime(data.freshness.announcements_checked_at)}</dd></div>
                <div className="col-span-2"><dt>Next scheduled refresh</dt><dd className="text-ink">{data.freshness.next_refresh ? fmtTime(data.freshness.next_refresh) : "—"}</dd></div>
              </dl>
              <details className="mt-3">
                <summary className="cursor-pointer text-ink-2">Analysis details</summary>
                <p className="mt-1.5 leading-relaxed">“Filings covered through” is the earliest of the holdings&apos; latest successful checks, so every holding was checked at least up to then. “Latest single update” can be one company&apos;s filing.
                  Filings: {data.freshness.sources.filings}. Prices: {data.freshness.sources.prices}. AI: {data.freshness.ai_mode}.
                  Times shown are exchange publication times (IST).</p>
              </details>
            </section>
          </aside>
        </div>
      )}
      </>)}
      </div>
    </div>
  );
}
