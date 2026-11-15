"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Freshness, Holding } from "@/lib/types";
import { ago, fmtDay, fmtPct, fmtPrice, tone } from "@/lib/format";
import { ErrorState, Skeleton, Sparkline } from "@/components/ui";

type Sort = "move" | "volume" | "events" | "name";
interface Summary { session_date: string | null; up: number; down: number; unchanged: number; unavailable: number; equal_weight_pct: number | null; equal_weight_n: number }
interface PortfolioResp { holdings: Holding[]; thresholds: { price_move_pct: number; volume_multiple: number }; freshness: Freshness; summary: Summary }

export default function PortfolioPage() {
  const [data, setData] = useState<PortfolioResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("move");
  const [manage, setManage] = useState(false);
  const [ticker, setTicker] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(() => { setErr(null); api<PortfolioResp>("/api/v1/portfolio").then(setData).catch((e) => setErr(e.message)); }, []);
  useEffect(load, [load]);

  const rows = useMemo(() => {
    const h = [...(data?.holdings || [])];
    const r = (x: Holding) => x.market?.return_pct.value ?? null;
    if (sort === "move") h.sort((a, b) => (r(a) === null ? 1 : 0) - (r(b) === null ? 1 : 0) || Math.abs(r(b) ?? 0) - Math.abs(r(a) ?? 0));
    if (sort === "name") h.sort((a, b) => a.ticker.localeCompare(b.ticker));
    if (sort === "volume") h.sort((a, b) => (b.market?.volume_ratio.value ?? -1) - (a.market?.volume_ratio.value ?? -1));
    if (sort === "events") h.sort((a, b) => b.important_announcements_7d - a.important_announcements_7d);
    return h;
  }, [data, sort]);

  const add = async () => {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    setBusy(true); setNote(null);
    try {
      const r = await api<{ ticker: string; company_name: string }>("/api/v1/portfolio/holdings", { method: "POST", body: JSON.stringify({ ticker: t }) });
      setNote({ ok: true, text: `Added ${r.company_name}. Its filings and prices are included from the next refresh.` });
      setTicker(""); load();
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }  // input kept on failure
  };
  const remove = async (t: string) => {
    try { await api(`/api/v1/portfolio/holdings/${t}`, { method: "DELETE" }); setNote({ ok: true, text: `Removed ${t}.` }); load(); }
    catch (e) { setNote({ ok: false, text: (e as Error).message }); }
  };

  const s = data?.summary;
  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="text-[30px] font-bold tracking-tight">Portfolio</h1>
      {s && data && (
        <div className="mt-2 space-y-3">
          <p className="text-[13px] font-medium text-ink-3">{data.holdings.length} holdings · {s.session_date ? `${fmtDay(s.session_date)} close` : "no price data yet"}</p>
          {s.equal_weight_pct !== null && (
            <div><p className={`num text-[40px] font-semibold leading-none tracking-tight ${tone(s.equal_weight_pct)}`}>{fmtPct(s.equal_weight_pct, 2)}</p>
              <p className="mt-1 text-[12.5px] text-ink-3">average daily move across holdings</p></div>
          )}
          <div className="grid max-w-md grid-cols-3 gap-2" data-testid="portfolio-counts" aria-label="Holdings moving up, down and unchanged">
            <div className="stat"><span className="block text-[12px] text-ink-3">Up</span><span className="num block text-[20px] font-semibold text-up">{s.up}</span></div>
            <div className="stat"><span className="block text-[12px] text-ink-3">Down</span><span className="num block text-[20px] font-semibold text-down">{s.down}</span></div>
            <div className="stat"><span className="block text-[12px] text-ink-3">Flat{s.unavailable > 0 ? " / n.a." : ""}</span><span className="num block text-[20px] font-semibold text-ink-2">{s.unchanged}{s.unavailable > 0 ? ` / ${s.unavailable}` : ""}</span></div>
          </div>
          <p className="text-[12px] text-ink-3">Average move = simple equal-weight average of daily moves across {s.equal_weight_n} holdings with a valid close. It is not your investment return.</p>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-1.5" role="group" aria-label="Sort holdings">
        {(["move", "volume", "events", "name"] as Sort[]).map((k) => (
          <button key={k} aria-pressed={sort === k} onClick={() => setSort(k)}
            className="chip">
            {{ move: "Biggest move", volume: "Volume", events: "Recent events", name: "A–Z" }[k]}
          </button>
        ))}
      </div>

      {err && <div className="mt-6"><ErrorState message={err} retry={load} /></div>}
      {!data && !err && <div className="mt-6 space-y-2">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>}
      {data && (
        <div className="card mt-4 overflow-hidden">
          <div className="hidden grid-cols-[minmax(0,1.6fr)_88px_100px_110px_90px] gap-3 border-b border-line px-4 py-2 text-[11px] font-medium uppercase tracking-wider text-ink-3 md:grid" aria-hidden>
            <span>Company</span><span>20 sessions</span><span className="text-right">Close</span><span className="text-right">Day · volume</span><span className="text-right">Events 7d</span>
          </div>
          <ul className="divide-y divide-line">
            {rows.map((h) => {
              const r = h.market?.return_pct.value ?? null;
              const vr = h.market?.volume_ratio.value ?? null;
              return (
                <li key={h.ticker}>
                  <Link href={`/research?ticker=${h.ticker}`} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-3 hover:bg-surface-2/50 md:grid-cols-[minmax(0,1.6fr)_88px_100px_110px_90px]"
                    aria-label={`${h.company_name}: ${h.market ? `close ${fmtPrice(h.market.close)}, ${r === null ? "day move unavailable" : `day move ${fmtPct(r)}`}` : "no price data"}. Open research.`}>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[14px] font-semibold">{h.ticker}</span>
                        {h.flags.price_move && <span className="rounded bg-accent/15 px-1.5 text-[11px] font-semibold text-accent">Move</span>}
                        {h.flags.volume_spike && <span className="rounded bg-info/15 px-1.5 text-[11px] font-semibold text-info">Volume</span>}
                        {h.important_announcements_7d > 0 && <span className="rounded bg-surface-2 px-1.5 text-[11px] text-ink-2 md:hidden">{h.important_announcements_7d} event{h.important_announcements_7d > 1 ? "s" : ""}</span>}
                      </div>
                      <p className="truncate text-[12px] text-ink-3">{h.company_name} · {h.sector}</p>
                    </div>
                    <Sparkline data={h.market?.sparkline || []} className="hidden h-6 w-20 md:block" />
                    <div className="text-right md:contents">
                      <p className="num text-[14px] md:text-right">{h.market ? fmtPrice(h.market.close) : <span className="text-[12px] text-ink-3">no data</span>}</p>
                      <p className={`num text-[12.5px] md:text-right ${tone(r)}`} title={h.market?.return_pct.note || ""}>
                        {h.market ? (r === null ? <span className="text-ink-3">unavailable</span> : fmtPct(r)) : ""}
                        {vr !== null && <span className="text-ink-3"> · {vr.toFixed(1)}× vol</span>}
                      </p>
                    </div>
                    <p className="hidden text-right text-[13px] text-ink-2 md:block">{h.important_announcements_7d || "—"}</p>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {data && (
        <p className="mt-3 text-[12px] leading-relaxed text-ink-3">
          End-of-day prices from {data.freshness.sources.prices} (fetched {ago(data.holdings[0]?.market?.fetched_at)}). “×” = the session&apos;s volume ÷ its
          20-session average. Sparklines show the last 20 closes; colour = change over those sessions (grey within ±0.5%). “Events” count grouped
          developments (several filings about one event count once). Flags use your Briefing thresholds: ±{data.thresholds.price_move_pct}% move,
          {" "}{data.thresholds.volume_multiple}× volume. “Unavailable” means the provider repeated the previous session&apos;s bar.
        </p>
      )}

      <section className="card mt-6">
        <button className="flex min-h-[48px] w-full items-center justify-between px-4 text-left" onClick={() => setManage((v) => !v)} aria-expanded={manage}>
          <span className="text-[14px] font-medium">Manage holdings</span><span className="text-[12.5px] text-ink-3">{manage ? "Close" : "Add or remove"}</span>
        </button>
        {manage && data && (
          <div className="border-t border-line p-4">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); add(); }}>
              <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="NSE symbol, e.g. ASIANPAINT" aria-label="NSE symbol to add"
                className="num min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-2 text-[14px] uppercase outline-none placeholder:normal-case focus:border-accent" />
              <button className="btn btn-primary" disabled={busy || !ticker.trim()}>{busy ? "Adding…" : "Add"}</button>
            </form>
            {note && <p role="status" className={`mt-2 text-[12.5px] ${note.ok ? "text-up" : "text-down"}`}>{note.text}</p>}
            <ul className="mt-3 flex flex-wrap gap-2">
              {data.holdings.map((h) => (
                <li key={h.ticker} className="flex items-center gap-1 rounded-full border border-line py-1 pl-3 pr-1 text-[12.5px]">
                  {h.ticker}
                  <button onClick={() => remove(h.ticker)} className="grid h-7 w-7 place-items-center rounded-full text-ink-3 hover:bg-surface-2 hover:text-down" aria-label={`Remove ${h.ticker}`}>✕</button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
