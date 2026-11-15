"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, docFileUrl } from "@/lib/api";
import type { Citation, Claim } from "@/lib/types";
import { fmtCr, fmtDay, fmtPct, fmtTime, tone } from "@/lib/format";

/** Evidence Lens: one global sheet that shows exactly where a claim comes from — the cited passage, its page,
 *  the reporting period — and lets the reader step through neighbouring passages or read the whole page. */

interface ChunkDetail {
  chunk_id: number; page_number: number; speaker: string | null; speaker_role: string | null; section: string | null; content: string;
  prev_chunk_id: number | null; next_chunk_id: number | null;
  document: { id: number; title: string; document_type: string; ticker: string; reporting_period: string | null; source_url: string;
    publication_date: string | null; pages: number | null; has_local_copy: boolean };
}
interface PageView {
  document_id: number; page: number; page_count: number | null; prev_page: number | null; next_page: number | null;
  passages: { chunk_id: number; speaker: string | null; speaker_role: string | null; content: string }[];
}

interface Growth { value: number | null; note: string | null }
interface CalcLine { metric: string; label: string; value: number | null; qoq: Growth; yoy: Growth; derived?: boolean;
  inputs?: { current: number | null; previous_quarter: number | null; year_ago_quarter: number | null } }
interface ResultsSnapshot { quarter: string; previous_quarter: string; year_ago_quarter: string; lines: CalcLine[]; basis: string | null;
  source_url: string | null; notes: string[]; formula?: { qoq: string; yoy: string };
  primary_filing?: { title: string; source_url: string; document_id: number | null; chunk_id: number | null; page_number: number | null } | null }

const crNum = (v: number | null | undefined) => (v === null || v === undefined ? "—" : fmtCr(v).replace(" cr", "").replace("₹", ""));
const growth = (g: Growth) => (g.value === null ? <span className="text-ink-3" title={g.note || ""}>n/m</span> : fmtPct(g.value));

/** Figures & calculations: every headline number shown with its inputs, period, basis, unit, formula and sources. */
function CalcView({ ticker, onOpen }: { ticker: string; onOpen: (c: Citation) => void }) {
  const [snap, setSnap] = useState<ResultsSnapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<{ latest_results: ResultsSnapshot | null }>(`/api/v1/companies/${ticker}/financials`)
      .then((r) => (r.latest_results ? setSnap(r.latest_results) : setErr("No filed results are available for this company yet.")))
      .catch((e) => setErr(e.message));
  }, [ticker]);
  if (err) return <p className="text-[13px] text-down">Could not load the figures: {err}</p>;
  if (!snap) return <div className="skeleton h-40" />;
  const pf = snap.primary_filing;
  return (
    <div className="space-y-4" data-testid="calc-view">
      <div className="no-scrollbar overflow-x-auto rounded-lg border border-line">
        <table className="w-full min-w-[420px] text-[12.5px]">
          <caption className="sr-only">{`${ticker} headline figures in ₹ crore for ${snap.quarter}, ${snap.previous_quarter} and ${snap.year_ago_quarter}, with quarter-on-quarter and year-on-year change`}</caption>
          <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-ink-3">
            <tr>
              <th scope="col" className="px-2.5 py-1.5 text-left font-medium">₹ crore</th>
              <th scope="col" className="px-2 text-right font-medium">{snap.quarter}</th>
              <th scope="col" className="px-2 text-right font-medium">{snap.previous_quarter}</th>
              <th scope="col" className="px-2 text-right font-medium">{snap.year_ago_quarter}</th>
              <th scope="col" className="px-2 text-right font-medium">QoQ</th>
              <th scope="col" className="px-2.5 text-right font-medium">YoY</th>
            </tr>
          </thead>
          <tbody>
            {snap.lines.map((l) => (
              <tr key={l.metric} className="border-t border-line">
                <th scope="row" className="px-2.5 py-1.5 text-left font-normal text-ink-2">{l.label}{l.derived ? <span className="text-ink-3"> (calc.)</span> : null}</th>
                <td className="num px-2 text-right">{crNum(l.inputs?.current ?? l.value)}</td>
                <td className="num px-2 text-right text-ink-2">{crNum(l.inputs?.previous_quarter)}</td>
                <td className="num px-2 text-right text-ink-2">{crNum(l.inputs?.year_ago_quarter)}</td>
                <td className={`num px-2 text-right ${tone(l.qoq.value)}`}>{growth(l.qoq)}</td>
                <td className={`num px-2.5 text-right ${tone(l.yoy.value)}`}>{growth(l.yoy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="grid gap-2.5 text-[12.5px] sm:grid-cols-2">
        <div><dt className="text-ink-3">Period</dt><dd className="text-ink">{snap.quarter}; compared with {snap.previous_quarter} (QoQ) and {snap.year_ago_quarter} (YoY)</dd></div>
        <div><dt className="text-ink-3">Basis · unit</dt><dd className="text-ink">{snap.basis ? snap.basis[0].toUpperCase() + snap.basis.slice(1) : "Not stated"} · ₹ crore</dd></div>
        {snap.formula && <div className="sm:col-span-2"><dt className="text-ink-3">How growth is calculated</dt>
          <dd className="text-ink">QoQ = {snap.formula.qoq}. YoY = {snap.formula.yoy}. Computed by FINN&apos;s finance module, not by the AI model; “n/m” = not meaningful (e.g. a loss in the base period).</dd></div>}
        {snap.notes.map((n, i) => <div key={i} className="text-ink-3 sm:col-span-2">{n}</div>)}
      </dl>
      <div className="space-y-2">
        <p className="label">Sources</p>
        {pf ? (
          <div className="rounded-lg border border-line p-3 text-[12.5px]">
            <p className="font-medium text-ink">Primary · the company&apos;s results filing</p>
            <p className="mt-0.5 text-ink-2">{pf.title}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {pf.chunk_id && <button className="btn btn-secondary" onClick={() => onOpen({ kind: "filing", chunk_id: pf.chunk_id, document_id: pf.document_id,
                page_number: pf.page_number, document_title: pf.title, source_url: pf.source_url, document_type: "exchange_filing" })}>Read the filing</button>}
              <a className="btn btn-quiet" href={pf.source_url} target="_blank" rel="noreferrer">Exchange PDF ↗</a>
            </div>
          </div>
        ) : (
          <p className="rounded-lg border border-line p-3 text-[12.5px] leading-relaxed text-ink-3">The company&apos;s {snap.quarter} results filing is not among the filings FINN has indexed, so the figures can&apos;t be linked to a page here. Check them against the filing on the exchange website.</p>
        )}
        <div className="rounded-lg border border-dashed border-line p-3 text-[12.5px]">
          <p className="font-medium text-ink-2">Secondary · standardised results table</p>
          <p className="mt-0.5 leading-relaxed text-ink-3">Line items as standardised from the filed results by Screener.in. Useful to locate a figure; the filing remains the authority.</p>
          {snap.source_url && <a className="mt-1 inline-block text-info" href={snap.source_url} target="_blank" rel="noreferrer">Open standardised table ↗</a>}
        </div>
      </div>
    </div>
  );
}

/** Exactly the figures a sentence cites: each fact with company, metric, period, value, unit, basis and source, and each
 *  calculation with its formula, worked expression and inputs (shown as rows too). Primary pages open in place. */
function ClaimView({ ids, claims, onOpen }: { ids: string[]; claims: Record<string, Claim>; onOpen: (c: Citation) => void }) {
  const order: string[] = [];
  const visit = (i: string) => { if (!claims[i] || order.includes(i)) return; order.push(i); claims[i].inputs.forEach(visit); };
  ids.forEach(visit);
  const rows = order.map((i) => claims[i]);
  const primaries = new Map<string, NonNullable<Claim["source"]["primary"]> & { forIds: string[] }>();
  const secondary = new Map<string, { title: string; url: string }>();
  rows.forEach((c) => {
    const p = c.source.primary;
    if (p?.chunk_id) {
      const k = `${p.document_id}:${p.page_number}`;
      const cur = primaries.get(k);
      if (cur) cur.forIds.push(c.id); else primaries.set(k, { ...p, forIds: [c.id] });
    }
    if (c.source.url) secondary.set(c.source.url, { title: c.source.title || c.source.kind, url: c.source.url });
  });
  return (
    <div className="space-y-4" data-testid="claim-view">
      <div className="no-scrollbar overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-[12.5px]">
          <caption className="sr-only">Cited figures with period, value, unit, basis and calculation inputs</caption>
          <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-ink-3">
            <tr><th scope="col" className="px-2.5 py-1.5 text-left font-medium">Figure</th><th scope="col" className="px-2 text-left font-medium">Period</th>
              <th scope="col" className="px-2.5 text-right font-medium">Value</th></tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className={`border-t border-line align-top ${ids.includes(c.id) ? "" : "text-ink-3"}`}>
                <th scope="row" className="px-2.5 py-1.5 text-left font-normal">
                  <span className={ids.includes(c.id) ? "text-ink" : ""}>{c.label}</span>
                  <span className="ml-1 text-[11px] text-ink-3">{c.id}{ids.includes(c.id) ? "" : " · input"}{c.basis ? ` · ${c.basis}` : ""}</span>
                  {c.kind === "calc" && (
                    <span className="mt-0.5 block text-[11.5px] text-ink-3">{c.formula}{c.expression ? ` → ${c.expression}` : ""}</span>
                  )}
                </th>
                <td className="num px-2 py-1.5">{c.period}{c.base_period ? ` vs ${c.base_period}` : ""}</td>
                <td className="num px-2.5 py-1.5 text-right">{c.display}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[12px] leading-relaxed text-ink-3">Company: {rows[0]?.company}. Units as shown (₹ crore unless stated). Calculations are
        computed by FINN&apos;s finance module from the input rows, not by the AI model.</p>
      <div className="space-y-2">
        <p className="label">Sources</p>
        {primaries.size > 0 && rows.some((c) => c.kind === "fact" && !c.source.primary?.chunk_id) && (
          <p className="rounded-lg border border-dashed border-line p-3 text-[12.5px] text-ink-3" data-testid="no-primary-inputs">
            No primary page matched for {rows.filter((c) => c.kind === "fact" && !c.source.primary?.chunk_id).map((c) => c.id).join(", ")}; those come from the secondary source only.</p>
        )}
        {primaries.size === 0 && <p className="rounded-lg border border-line p-3 text-[12.5px] text-ink-3">No page in the company&apos;s own filings or annual report was matched to these figures, so only the secondary source is shown.</p>}
        {Array.from(primaries.values()).map((p) => (
          <div key={`${p.document_id}:${p.page_number}`} className="rounded-lg border border-line p-3 text-[12.5px]">
            <p className="font-medium text-ink">Primary · {p.title}{p.page_number ? `, p.${p.page_number}` : ""}</p>
            <p className="mt-0.5 text-ink-3">For {p.forIds.join(", ")}{p.column ? ` (${p.column})` : ""}</p>
            {p.excerpt && <p className="mt-1 rounded bg-surface-2/60 px-2 py-1 text-[12px] text-ink-2">“{p.excerpt}”</p>}
            {(p as { printed_value?: string }).printed_value && <p className="mt-1 text-[12px] text-ink-3">Printed as {(p as { printed_value?: string }).printed_value} (₹ crore); the standardised table rounds it.</p>}
            <button className="btn btn-secondary mt-2" onClick={() => onOpen({ kind: "filing", chunk_id: p.chunk_id, document_id: p.document_id,
              page_number: p.page_number, document_title: p.title, source_url: p.source_url, document_type: "annual_report" })}>Open page {p.page_number}</button>
          </div>
        ))}
        {Array.from(secondary.values()).map((s) => (
          <div key={s.url} className="rounded-lg border border-dashed border-line p-3 text-[12.5px]">
            <p className="font-medium text-ink-2">Secondary · {s.title}</p>
            <a className="mt-1 inline-block text-info" href={s.url} target="_blank" rel="noreferrer">Open ↗</a>
          </div>
        ))}
      </div>
    </div>
  );
}

const Ctx = createContext<(c: Citation) => void>(() => {});
export const useEvidence = () => useContext(Ctx);

const TYPE_LABEL: Record<string, string> = {
  concall_transcript: "Earnings-call transcript", annual_report: "Annual report", exchange_filing: "Exchange filing",
  presentation: "Investor presentation", standardised_results: "Filed results (standardised table)",
};

function Meta({ label, value }: { label: string; value?: string | number | null }) {
  if (value === null || value === undefined || value === "") return null;
  return <div className="min-w-0"><dt className="text-[11px] uppercase tracking-wider text-ink-3">{label}</dt><dd className="truncate text-[12px] text-ink">{value}</dd></div>;
}

export function EvidenceProvider({ children }: { children: React.ReactNode }) {
  const [cit, setCit] = useState<Citation | null>(null);
  const [chunkId, setChunkId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ChunkDetail | null>(null);
  const [view, setView] = useState<"passage" | "page">("passage");
  const [page, setPage] = useState<PageView | null>(null);
  const [pageNo, setPageNo] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const returnFocus = useRef<HTMLElement | null>(null);
  const closeBtn = useRef<HTMLButtonElement | null>(null);
  const open = useCallback((c: Citation) => {
    if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement && !document.activeElement.closest("[data-evidence]")) {
      returnFocus.current = document.activeElement;
    }
    setCit(c); setChunkId(c.chunk_id ?? null); setDetail(null); setErr(null); setView("passage"); setPage(null); setPageNo(null);
  }, []);
  // Closing returns focus to whatever opened the evidence, without scrolling, so the reader keeps their place.
  const close = useCallback(() => {
    setCit(null);
    setTimeout(() => returnFocus.current?.focus({ preventScroll: true }), 0);
  }, []);
  useEffect(() => { if (cit) closeBtn.current?.focus({ preventScroll: true }); }, [cit]);

  useEffect(() => {
    if (!chunkId) return;
    setErr(null);
    api<ChunkDetail>(`/api/v1/evidence/chunks/${chunkId}`).then((d) => { setDetail(d); setPageNo(d.page_number); }).catch((e) => setErr(e.message));
  }, [chunkId]);
  useEffect(() => {
    if (view !== "page" || !detail || !pageNo) return;
    api<PageView>(`/api/v1/documents/${detail.document.id}/pages/${pageNo}`).then(setPage).catch((e) => setErr(e.message));
  }, [view, detail, pageNo]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  const docId = detail?.document.id ?? cit?.document_id;
  const shownPage = view === "page" ? pageNo : detail?.page_number ?? cit?.page_number;
  const totalPages = detail?.document.pages;
  const isOriginal = detail?.chunk_id === cit?.chunk_id;
  const isClaims = cit?.kind === "claims";
  const calc = cit?.kind === "structured_results" || isClaims;
  return (
    <Ctx.Provider value={open}>
      {children}
      {cit && (
        <div data-evidence className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-[2px] md:items-stretch md:justify-end md:bg-black/40" onClick={close} role="dialog" aria-modal aria-label={calc ? "Figures and calculations" : "Evidence"}>
          {/* Mobile: bottom sheet. Desktop: right-hand drawer so the answer or briefing stays readable beside it. */}
          <div className="sheet flex max-h-[90dvh] w-full max-w-2xl flex-col rounded-t-[28px] border border-line bg-surface shadow-[var(--shadow-float)] md:h-dvh md:max-h-none md:w-[520px] md:max-w-[94vw] md:rounded-none md:border-y-0 md:border-r-0" onClick={(e) => e.stopPropagation()}>
            <div className="border-b border-line p-5 pb-3">
              <div className="mx-auto mb-3 h-1.5 w-11 rounded-full bg-line-strong md:hidden" aria-hidden />
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[11px] font-medium uppercase tracking-wider text-accent">{calc ? "Figures & calculations" : `Evidence${cit.id ? ` · ${cit.id}` : ""}`}</p>
                  <h3 className="mt-1 text-[17px] font-semibold leading-snug tracking-tight">{isClaims ? `${cit.claim_ids?.length ?? 0} cited figure${cit.claim_ids?.length === 1 ? "" : "s"}` : calc ? `${cit.ticker ? `${cit.ticker} · ` : ""}latest quarterly results` : detail?.document.title || cit.document_title}</h3>
                </div>
                <button ref={closeBtn} onClick={close} className="-mr-1 -mt-1 grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface-2 text-ink-2 hover:bg-surface-3 hover:text-ink" aria-label="Close evidence">✕</button>
              </div>
              {!calc && <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
                <Meta label="Source type" value={TYPE_LABEL[detail?.document.document_type || cit.document_type || ""] || cit.document_type} />
                <Meta label="Company" value={detail?.document.ticker} />
                <Meta label="Period" value={detail?.document.reporting_period || cit.reporting_period} />
                <Meta label="Page" value={shownPage ? `${shownPage}${totalPages ? ` of ${totalPages}` : ""}` : null} />
                <Meta label="Published" value={cit.publication_date ? (cit.publication_date.length > 10 ? fmtTime(cit.publication_date) : fmtDay(cit.publication_date)) : detail?.document.publication_date ? fmtDay(detail.document.publication_date) : null} />
                <Meta label="Speaker" value={detail?.speaker || cit.speaker ? `${detail?.speaker || cit.speaker}${(detail?.speaker_role || cit.speaker_role) ? ` (${detail?.speaker_role || cit.speaker_role})` : ""}` : null} />
              </dl>}
              {detail && (
                <div className="mt-3 flex gap-1 text-[12px]" role="tablist">
                  {(["passage", "page"] as const).map((v) => (
                    <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
                      className={`min-h-[32px] rounded-full px-3 ${view === v ? "bg-ink text-bg" : "border border-line text-ink-2"}`}>{v === "passage" ? "Cited passage" : "Whole page"}</button>
                  ))}
                </div>
              )}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-5 pt-4">
              {isClaims && cit.claims && <ClaimView ids={cit.claim_ids || []} claims={cit.claims} onOpen={open} />}
              {calc && !isClaims && (cit.ticker ? <CalcView ticker={cit.ticker} onOpen={open} /> : <p className="text-[13px] leading-relaxed text-ink-2">{cit.relevant_excerpt}</p>)}
              {!calc && view === "passage" && (
                <>
                  {!isOriginal && detail && <p className="mb-2 text-[11px] text-ink-3">Neighbouring passage (not the one cited) · <button className="text-info" onClick={() => setChunkId(cit.chunk_id ?? null)}>back to cited passage</button></p>}
                  <blockquote className={`whitespace-pre-line border-l-2 ${isOriginal || !detail ? "border-accent" : "border-line"} bg-surface-2/60 py-3 pl-4 pr-3 text-[13.5px] leading-relaxed text-ink`} data-testid="evidence-passage">
                    {detail?.content || cit.relevant_excerpt || "No excerpt stored for this source."}
                  </blockquote>
                  {detail && (
                    <div className="mt-3 flex justify-between text-[12px]">
                      <button disabled={!detail.prev_chunk_id} onClick={() => setChunkId(detail.prev_chunk_id)} className="min-h-[36px] rounded-md border border-line px-3 text-ink-2 disabled:opacity-30">← Previous passage</button>
                      <button disabled={!detail.next_chunk_id} onClick={() => setChunkId(detail.next_chunk_id)} className="min-h-[36px] rounded-md border border-line px-3 text-ink-2 disabled:opacity-30">Next passage →</button>
                    </div>
                  )}
                </>
              )}
              {!calc && view === "page" && (
                page ? (
                  <div className="space-y-3" data-testid="evidence-page">
                    {page.passages.map((p) => (
                      <p key={p.chunk_id} className={`whitespace-pre-line rounded-md px-2 py-1.5 text-[13px] leading-relaxed ${p.chunk_id === cit.chunk_id ? "bg-accent/15 text-ink ring-1 ring-accent/40" : "text-ink-2"}`}>
                        {p.speaker && <span className="mr-1 font-medium text-ink">{p.speaker}:</span>}{p.content}
                      </p>
                    ))}
                    <div className="flex justify-between pt-1 text-[12px]">
                      <button disabled={!page.prev_page} onClick={() => setPageNo(page.prev_page)} className="min-h-[36px] rounded-md border border-line px-3 text-ink-2 disabled:opacity-30">← Page {page.prev_page ?? ""}</button>
                      <button disabled={!page.next_page} onClick={() => setPageNo(page.next_page)} className="min-h-[36px] rounded-md border border-line px-3 text-ink-2 disabled:opacity-30">Page {page.next_page ?? ""} →</button>
                    </div>
                  </div>
                ) : <div className="skeleton h-40" />
              )}
              {err && <p className="mt-2 text-xs text-down">Could not load: {err}</p>}
              {cit.relevant_excerpt?.startsWith("[index summary]") && (
                <p className="mt-3 text-[11px] text-ink-3">FINN hasn&apos;t extracted this filing&apos;s text yet, so the excerpt is the index&apos;s one-line summary. Open the original to verify.</p>
              )}
            </div>

            <div className="flex flex-wrap gap-2 border-t border-line p-4 pb-[max(1rem,env(safe-area-inset-bottom))] empty:hidden">
              {!calc && docId && detail?.document.has_local_copy && (
                <a className="btn btn-primary" href={docFileUrl(docId, shownPage)} target="_blank" rel="noreferrer">Open PDF at page {shownPage}</a>
              )}
              {!calc && (detail?.document.source_url || cit.source_url) && (
                <a className="btn btn-secondary" href={detail?.document.source_url || cit.source_url || "#"} target="_blank" rel="noreferrer">Original source ↗</a>
              )}
            </div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}

export function CiteChip({ c, label }: { c: Citation; label?: string }) {
  const open = useEvidence();
  return (
    <button onClick={() => open(c)} className="inline-flex min-h-[24px] items-center gap-1 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 align-baseline text-[11px] text-ink-2 hover:border-accent hover:text-ink"
      title={c.document_title || ""} aria-label={`Open evidence: ${c.document_title || "source"}${c.page_number ? `, page ${c.page_number}` : ""}`}>
      <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden><path d="M7 3h7l5 5v13H7zM14 3v5h5" /></svg>
      {label || (c.page_number ? `p.${c.page_number}` : "source")}
    </button>
  );
}
