"use client";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Citation, KeyMetric } from "@/lib/types";
import { fmtCr, fmtPct, fmtPp, tone } from "@/lib/format";
import { CiteChip } from "./EvidenceSheet";

export interface Financials {
  ticker: string; is_financial: boolean; basis: string | null; source_url: string | null; fetched_at: string | null;
  quarterly: Record<string, number | null | string>[];
  annual: { years: Record<string, number | null | string>[]; cagr: Record<string, { value: number | null; note: string | null }> };
  latest_results: { quarter: string; lines: KeyMetric[]; margin: { label: string; value: number | null; year_ago: number | null; change_yoy_pp: { value: number | null } }; notes: string[] } | null;
}

const n = (v: unknown) => (typeof v === "number" ? v : null);

export function Numbers({ f }: { f: Financials }) {
  const marginKey = f.is_financial ? "pat_margin_pct" : "opm_pct";
  const q = f.quarterly.slice(-12).map((r) => ({ period: String(r.period), revenue: n(r.revenue), pat: n(r.pat), margin: n(r[marginKey]) }));
  // Opens the figures-and-calculations view: inputs, formula, basis, and the primary results filing where available.
  const src: Citation = { kind: "structured_results", ticker: f.ticker, document_title: "Figures & calculations", source_url: f.source_url, document_type: "standardised_results" };
  const topLine = f.is_financial ? "Interest earned" : "Revenue";
  const marginLabel = f.is_financial ? "PAT ÷ interest earned" : "operating margin";
  const lr = f.latest_results;
  const years = f.annual.years.slice(-6);
  if (!q.length) return <p className="text-sm text-ink-3">No structured results available for this company.</p>;
  return (
    <div className="space-y-6">
      {lr && (
        <section>
          <div className="flex items-center justify-between gap-2"><h3 className="text-[14px] font-semibold">{lr.quarter} results · ₹ crore · {f.basis}</h3><CiteChip c={src} label="inputs & source" /></div>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {lr.lines.map((l) => (
              <div key={l.metric} className="rounded-xl border border-line bg-surface p-3">
                <p className="text-[11px] uppercase tracking-wider text-ink-3">{l.label}</p>
                <p className="num mt-1 text-[15px]">{fmtCr(l.value)}</p>
                <p className="num mt-0.5 text-[11px]"><span className={tone(l.yoy.value)}>{l.yoy.value === null ? "n/m" : fmtPct(l.yoy.value)}</span> <span className="text-ink-3">YoY</span> · <span className={tone(l.qoq.value)}>{l.qoq.value === null ? "n/m" : fmtPct(l.qoq.value)}</span> <span className="text-ink-3">QoQ</span></p>
              </div>
            ))}
          </div>
          {lr.margin.value !== null && <p className="mt-2 text-[12px] text-ink-2">{lr.margin.label.replace("*", "")}: <span className="num">{lr.margin.value.toFixed(1)}%</span> (<span className={`num ${tone(lr.margin.change_yoy_pp.value)}`}>{lr.margin.change_yoy_pp.value === null ? "n/m" : fmtPp(lr.margin.change_yoy_pp.value)}</span> YoY)</p>}
          {lr.notes.map((x, i) => <p key={i} className="mt-1 text-[12px] text-ink-3">{x}</p>)}
        </section>
      )}
      <section>
        <h3 className="text-[14px] font-semibold">Quarterly {topLine.toLowerCase()}, PAT &amp; {marginLabel} · last {q.length} quarters</h3>
        <div className="mt-2 h-56 rounded-xl border border-line bg-surface p-2" role="img"
          aria-label={`Chart of quarterly ${topLine.toLowerCase()} and PAT in rupees crore with ${marginLabel}, ${q[0]?.period} to ${q[q.length - 1]?.period}. The same figures are available as a table below the chart.`}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={q} margin={{ top: 8, right: 4, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="period" tick={{ fontSize: 9, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
              <YAxis yAxisId="l" tick={{ fontSize: 9, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${Math.round(v / 1000)}k`} />
              <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 9, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} unit="%" domain={["auto", "auto"]} />
              <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 11 }}
                formatter={(v: number, name: string) => (name === "margin" ? [`${v?.toFixed(1)}%`, "Margin"] : [fmtCr(v), name === "revenue" ? topLine : "PAT"])} />
              <Bar yAxisId="l" dataKey="revenue" fill="var(--surface-2)" stroke="var(--line)" radius={[3, 3, 0, 0]} />
              <Bar yAxisId="l" dataKey="pat" fill="var(--accent)" radius={[3, 3, 0, 0]} />
              <Line yAxisId="r" dataKey="margin" stroke="var(--info)" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-1 text-[12px] text-ink-3">Grey bars: {topLine.toLowerCase()} · amber bars: PAT (₹ cr, left axis) · blue line: {marginLabel} (%, right axis). {f.basis} basis.</p>
        <details className="mt-1 text-[12px]">
          <summary className="cursor-pointer py-1 text-ink-2">Show chart data as a table</summary>
          <div className="no-scrollbar mt-1 overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[360px] text-[12px]">
              <caption className="sr-only">Quarterly {topLine.toLowerCase()}, PAT and {marginLabel}</caption>
              <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-ink-3"><tr><th scope="col" className="px-3 py-1.5 text-left font-medium">Quarter</th>
                <th scope="col" className="px-2 text-right font-medium">{topLine} (₹ cr)</th><th scope="col" className="px-2 text-right font-medium">PAT (₹ cr)</th><th scope="col" className="px-3 text-right font-medium">Margin</th></tr></thead>
              <tbody>{q.map((r) => (
                <tr key={r.period} className="border-t border-line"><th scope="row" className="px-3 py-1 text-left font-normal text-ink-2">{r.period}</th>
                  <td className="num px-2 text-right">{fmtCr(r.revenue).replace(" cr", "")}</td><td className="num px-2 text-right">{fmtCr(r.pat).replace(" cr", "")}</td>
                  <td className="num px-3 text-right">{r.margin === null ? "—" : `${r.margin.toFixed(1)}%`}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </details>
      </section>
      <section>
        <h3 className="text-[14px] font-semibold">{f.is_financial ? "Multi-year trajectory and returns" : "Multi-year trajectory, balance sheet & cash quality"}</h3>
        <div className="no-scrollbar mt-2 overflow-x-auto rounded-xl border border-line">
          <table className="w-full min-w-[420px] text-[12px]">
            <caption className="sr-only">Annual figures in ₹ crore with margins and returns</caption>
            <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-ink-3">
              <tr><th scope="col" className="px-3 py-2 text-left font-medium">Year</th><th scope="col" className="px-2 text-right font-medium">{topLine}</th><th scope="col" className="px-2 text-right font-medium">PAT</th><th scope="col" className="px-2 text-right font-medium">Margin</th>
                {!f.is_financial && <><th scope="col" className="px-2 text-right font-medium">CFO/PAT</th><th scope="col" className="px-2 text-right font-medium">D/E</th></>}<th scope="col" className="px-3 text-right font-medium">{f.is_financial ? "ROE" : "ROCE"}</th></tr>
            </thead>
            <tbody>
              {years.map((y) => (
                <tr key={String(y.period)} className="border-t border-line">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal text-ink-2">{String(y.period)}</th>
                  <td className="num px-2 text-right">{fmtCr(n(y.revenue))}</td>
                  <td className="num px-2 text-right">{fmtCr(n(y.pat))}</td>
                  <td className="num px-2 text-right">{n(y[marginKey]) !== null ? `${n(y[marginKey])!.toFixed(0)}%` : "—"}</td>
                  {!f.is_financial && <>
                    <td className={`num px-2 text-right ${n(y.cash_conversion) !== null && n(y.cash_conversion)! < 0.8 ? "text-down" : ""}`}>{n(y.cash_conversion) !== null ? `${n(y.cash_conversion)!.toFixed(2)}×` : "—"}</td>
                    <td className="num px-2 text-right">{n(y.debt_to_equity) !== null ? `${n(y.debt_to_equity)!.toFixed(2)}×` : "—"}</td></>}
                  <td className="num px-3 text-right">{n(f.is_financial ? y.roe_pct : y.roce_pct) !== null ? `${n(f.is_financial ? y.roe_pct : y.roce_pct)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
          ₹ crore. {Object.entries(f.annual.cagr).map(([k, v]) => `${k.replace(/_/g, " ").replace(" pct", "")}: ${v.value !== null ? v.value.toFixed(1) + "%" : v.note}`).join(" · ")}
          {f.is_financial ? " · Debt/equity and cash conversion are left out: they are not meaningful for lenders." : " · CFO/PAT = operating cash flow ÷ profit after tax; below 0.8× is highlighted."}
        </p>
      </section>
    </div>
  );
}
