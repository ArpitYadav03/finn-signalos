"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { Citation, PromiseTracker, ResearchResponse, Signal, Snapshot } from "@/lib/types";
import { fmtDay, fmtPct, fmtPrice, fmtTime, tone } from "@/lib/format";
import { Answer, FiguresTable } from "@/components/Answer";
import { CiteChip, useEvidence } from "@/components/EvidenceSheet";
import { Numbers, type Financials } from "@/components/Numbers";
import { Promises } from "@/components/Promises";
import { Hypotheses } from "@/components/Hypotheses";
import { safeReturn } from "@/lib/researchLink";
import Link from "next/link";
import { SignalCard } from "@/components/SignalCard";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";

const DEMOS = ["TCS", "ITC", "HDFCBANK", "INFY", "MARUTI"];
// Suggested questions differ by business model: lenders are read through interest income and asset quality, not EBITDA or cash conversion.
const SUGGESTED_GENERAL = [
  "What's the latest from the company?",
  "Why did the stock move recently?",
  "What did management promise, and did they deliver?",
  "Is cash flow keeping up with reported profit?",
  "How has the business changed over five years?",
];
const SUGGESTED_LENDER = [
  "What's the latest from the bank?",
  "Why did the stock move recently?",
  "How has net interest income trended?",
  "What has management said about asset quality?",
  "What did management promise, and did they deliver?",
];
type Tab = "ask" | "evidence" | "numbers" | "promises" | "sources";
interface Turn { role: "user" | "assistant"; content: string; response?: ResearchResponse; error?: string; question?: string }
const TOOL_LABEL: Record<string, string> = {
  get_recent_filings: "latest filings", read_filing: "read a filing", read_filing_passages: "read filing passages", get_price_moves: "end-of-day prices",
  get_financial_snapshot: "quarterly results", get_annual_overview: "multi-year financials", search_documents: "searched documents",
  get_promise_tracker: "promise ledger", calculate_growth: "growth calculator", calculate_margin: "margin calculator",
};

interface Profile { status: string; text?: string; citations?: Citation[]; document_title?: string; reason?: string; method?: string }
interface CompanyResp {
  ticker: string; company_name: string; sector: string | null; is_financial: boolean; in_portfolio: boolean;
  market: Snapshot | null;
}
interface DocRow { id: number; title: string; document_type: string; reporting_period: string | null; publication_date: string | null; source_url: string; status: string; pages: number | null; error: string | null }

// Conversation survives navigating away and back (per company, this browser tab only).
const threadKey = (t: string) => `finn.thread.${t}`;
function loadThread(t: string): Turn[] {
  try { const raw = sessionStorage.getItem(threadKey(t)); return raw ? (JSON.parse(raw) as Turn[]) : []; } catch { return []; }
}
function saveThread(t: string, turns: Turn[]) {
  try { if (turns.length) sessionStorage.setItem(threadKey(t), JSON.stringify(turns.slice(-12))); else sessionStorage.removeItem(threadKey(t)); } catch { /* storage unavailable */ }
}

function Legend() {
  return (
    <p className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11.5px] text-ink-3" data-testid="answer-legend">
      <span className="inline-flex items-center gap-1"><span className="rounded border border-line bg-surface-2 px-1 text-ink-2">E1 · p.4</span> quoted from a company document</span>
      <span className="inline-flex items-center gap-1"><span className="rounded border border-line bg-surface-2 px-1 text-ink-2">FY26 CFO</span> a reported figure or FINN calculation — opens its period, basis, inputs and source</span>
      <span>no marker = interpretation; causes are stated only when a cited passage states them</span>
    </p>
  );
}

function AnswerBlock({ t, onFollowUp }: { t: Turn; onFollowUp: (q: string) => void }) {
  const r = t.response!;
  const openEv = useEvidence();
  const claims = r.claims || {};
  const v = r.verification;
  const ai = r.mode.startsWith("llm");
  const checked = Array.from(new Set(r.steps.map((s) => TOOL_LABEL[String(s.tool)] || String(s.tool))));
  return (
    <article data-testid="research-answer">
      {(() => {
        // Conclusion first, then the figures it rests on (one table, with units and periods), then evidence and open questions.
        const cut = t.content.search(/\n\s*\*\*\s*(evidence|open questions?)/i);
        const head = cut > 0 ? t.content.slice(0, cut) : t.content;
        const rest = cut > 0 ? t.content.slice(cut) : "";
        const n = Object.keys(claims).length;
        return (
          <>
            <Answer md={head} evidence={r.evidence} claims={claims} />
            {n > 0 && n <= 10 && (
              <div className="mt-3" data-testid="relevant-figures">
                <p className="label mb-1">Relevant figures</p>
                <FiguresTable claims={claims} onOpen={(ids) => openEv({ kind: "claims", claim_ids: ids, claims, document_title: "Figures & calculations" })} />
              </div>
            )}
            {rest && <div className="mt-3"><Answer md={rest} evidence={r.evidence} claims={claims} /></div>}
          </>
        );
      })()}
      {r.evidence.length === 0 && !r.metrics_used.length && (
        <p className="mt-3 rounded-lg border border-line px-3 py-2 text-[12.5px] text-ink-2">
          <span className="font-medium text-ink">Insufficient evidence.</span> None of the documents FINN has indexed for this company address the question directly.
        </p>
      )}
      {r.caveats.length > 0 && (
        <div className="mt-3 rounded-lg bg-surface-2/70 p-3 text-[12px] leading-relaxed text-ink-2">
          <p className="label">Limitations</p>
          <ul className="mt-1 space-y-0.5">{r.caveats.map((c, j) => <li key={j}>{c}</li>)}</ul>
        </div>
      )}
      {r.follow_up && (
        <button onClick={() => onFollowUp(r.follow_up!)} className="mt-3 flex min-h-[40px] w-full items-center gap-2 rounded-lg border border-line px-3 text-left text-[13px] text-ink-2 hover:border-accent hover:text-ink">
          <span className="text-accent" aria-hidden>→</span><span><span className="sr-only">Ask follow-up: </span>{r.follow_up}</span>
        </button>
      )}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
        {Object.keys(claims).length > 10 && (
          <details className="w-full min-w-0">
            <summary className="cursor-pointer py-1 text-ink-2">Figures used ({Object.keys(claims).length})</summary>
            <div className="mt-2"><FiguresTable claims={claims} onOpen={(ids) => openEv({ kind: "claims", claim_ids: ids, claims, document_title: "Figures & calculations" })} /></div>
          </details>
        )}
        {r.evidence.length > 0 && (
          <details className="min-w-0">
            <summary className="cursor-pointer py-1 text-ink-2">Sources ({r.evidence.length})</summary>
            <ul className="mt-2 space-y-2">
              {r.evidence.map((e) => (
                <li key={e.id} className="flex items-start gap-2 text-[12px] text-ink-2"><CiteChip c={e} label={e.id} />
                  <span className="min-w-0"><span className="text-ink">{e.document_title}</span>{e.page_number ? ` · p.${e.page_number}` : ""}{e.reporting_period ? ` · ${e.reporting_period}` : ""}{e.speaker ? ` · ${e.speaker}` : ""}</span></li>
              ))}
            </ul>
          </details>
        )}
        <details className="min-w-0">
          <summary className="cursor-pointer py-1 text-ink-3">Analysis details</summary>
          <dl className="mt-1.5 space-y-1 text-[12px] text-ink-3">
            <div><dt className="inline text-ink-2">Mode: </dt><dd className="inline">{ai ? `AI-written from retrieved sources (${r.mode.slice(4)})` : "Extractive — computed figures and verbatim quotes, no AI model"}</dd></div>
            {checked.length > 0 && <div><dt className="inline text-ink-2">Checked: </dt><dd className="inline">{checked.join(", ")}</dd></div>}
            <div><dt className="inline text-ink-2">Checks: </dt><dd className="inline">{v ? `${v.figures_checked} figure${v.figures_checked === 1 ? "" : "s"} matched to their cited fact, calculation or passage on value, sign, unit, period, basis and company` +
              (v.removed.length ? `; ${v.removed.length} statement${v.removed.length === 1 ? "" : "s"} removed` : "") +
              (v.explanations_removed.length ? `; ${v.explanations_removed.length} unsupported explanation${v.explanations_removed.length === 1 ? "" : "s"} removed` : "") +
              ". These checks confirm figures match sources; they don't judge the interpretation." : "citation markers validated against retrieved passages."}</dd></div>
            <div><dt className="inline text-ink-2">Prompt · trace: </dt><dd className="num inline">{r.prompt_version} · {r.trace_id.slice(0, 8)}</dd></div>
          </dl>
        </details>
      </div>
    </article>
  );
}

/** The company's own most material developments (last 30 days), the same event cards as the Briefing — for any ticker. */
function LatestDevelopments({ ticker }: { ticker: string }) {
  const [cards, setCards] = useState<Signal[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    // Material events, plus any routine-scored event that carries a verified guidance connection (e.g. an earnings call).
    api<{ signals: Signal[]; routine: Signal[] }>(`/api/v1/signals?hours=720&ticker=${encodeURIComponent(ticker)}`)
      .then((b) => setCards([...b.signals, ...b.routine.filter((r) => (r.promise_links || []).length > 0)])).catch(() => setCards([]));
  }, [ticker]);
  if (!cards || cards.length === 0) return null;
  const shown = open ? cards : cards.slice(0, 1);
  return (
    <section className="mt-3 space-y-2" aria-label="Latest developments" data-testid="latest-developments">
      <p className="label">Latest material developments · last 30 days · {cards.length}</p>
      {shown.map((c) => <SignalCard key={c.id} s={c} />)}
      {cards.length > 1 && <button className="btn btn-quiet" onClick={() => setOpen((v) => !v)}>{open ? "Show fewer" : `Show ${cards.length - 1} more`}</button>}
    </section>
  );
}

function ResearchInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const ticker = (sp.get("ticker") || "").toUpperCase();
  const filingId = sp.get("filing");
  const initialQ = sp.get("q") || "";
  const backTo = safeReturn(sp.get("from"));

  const [input, setInput] = useState(ticker);
  const [company, setCompany] = useState<CompanyResp | null>(null);
  const [cErr, setCErr] = useState<{ msg: string; notFound: boolean } | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [tab, setTab] = useState<Tab>(sp.get("tab") === "evidence" ? "evidence" : "ask");
  const [filing, setFiling] = useState<Signal | null>(null);
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState<Turn[]>([]);
  const [aiOn, setAiOn] = useState<boolean | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [asking, setAsking] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [tabErr, setTabErr] = useState<string | null>(null);
  const [fin, setFin] = useState<Financials | null>(null);
  const [pt, setPt] = useState<PromiseTracker | null>(null);
  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const autoAsked = useRef<string | null>(null);

  const loadCompany = useCallback(async (t: string) => {
    setCompany(null); setCErr(null); setFin(null); setPt(null); setDocs(null); setTabErr(null); setThread(loadThread(t));
    try {
      setCompany(await api<CompanyResp>(`/api/v1/companies/${t}`));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Not seen before: resolve it live from the document index and primary sources.
        setIndexing(true);
        try {
          await api(`/api/v1/companies/${t}/index`, { method: "POST" });
          setCompany(await api<CompanyResp>(`/api/v1/companies/${t}`));
        } catch (e2) {
          setCErr({ msg: (e2 as Error).message, notFound: e2 instanceof ApiError && e2.status === 404 });
        } finally {
          setIndexing(false);
        }
      } else setCErr({ msg: (e as Error).message, notFound: false });
    }
  }, []);

  useEffect(() => { if (ticker) { setInput(ticker); loadCompany(ticker); } }, [ticker, loadCompany]);
  useEffect(() => { api<{ enabled: boolean }>("/api/v1/ai/status").then((a) => setAiOn(a.enabled)).catch(() => setAiOn(null)); }, []);
  useEffect(() => {
    if (!ticker) return;
    // Save completed turns only: a question still being answered (or one that failed) must not come back without its answer.
    const done = thread.filter((t) => !t.error);
    while (done.length && done[done.length - 1].role === "user") done.pop();
    saveThread(ticker, done);
  }, [ticker, thread]);
  useEffect(() => {
    // Business description from the company's own annual report; poll briefly while it is being indexed.
    if (!company) { setProfile(null); return; }
    let cancelled = false, tries = 0;
    const load = () => api<Profile>(`/api/v1/companies/${company.ticker}/profile`).then((p) => {
      if (cancelled) return;
      setProfile(p);
      if (p.status === "indexing" && tries++ < 8) setTimeout(load, 8000);
    }).catch(() => !cancelled && setProfile({ status: "error" }));
    load();
    return () => { cancelled = true; };
  }, [company]);
  useEffect(() => { if (asking || thread.length) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [thread.length, asking]);
  useEffect(() => {
    if (filingId) api<Signal>(`/api/v1/signals/${filingId}`).then(setFiling).catch(() => setFiling(null));
    else setFiling(null);
  }, [filingId]);

  const ask = useCallback(async (q: string, prior: Turn[]) => {
    if (!ticker || q.trim().length < 2) return;
    setAsking(true); setTab("ask"); setQuestion("");
    const clean = prior.filter((t) => !t.error);
    const history = clean.map((t) => ({ role: t.role, content: t.content }));
    setThread([...clean, { role: "user", content: q }]);
    try {
      const r = await api<ResearchResponse>("/api/v1/research", { method: "POST",
        body: JSON.stringify({ ticker, question: q, filing_id: filingId ? Number(filingId) : null, history }) });
      setThread((t) => [...t, { role: "assistant", content: r.answer_markdown, response: r }]);
      setPt(null); setDocs(null); // the corpus may have grown while answering
    } catch (e) {
      // Keep the question so the reader can retry or edit it.
      setQuestion(q);
      setThread((t) => [...t.slice(0, -1), { role: "assistant", content: "", error: (e as Error).message, question: q }]);
    } finally { setAsking(false); }
  }, [ticker, filingId]);

  useEffect(() => {
    const key = `${ticker}|${initialQ}|${filingId}`;
    if (company && initialQ && autoAsked.current !== key) {
      autoAsked.current = key;
      const prior = loadThread(ticker);
      if (!prior.some((t) => t.role === "user" && t.content === initialQ)) ask(initialQ, prior);
    }
  }, [company, initialQ, ticker, filingId, ask]);

  useEffect(() => {
    if (!company) return;
    setTabErr(null);
    if (tab === "numbers" && !fin) api<Financials>(`/api/v1/companies/${ticker}/financials`).then(setFin).catch((e) => setTabErr(e.message));
    if (tab === "promises" && !pt) api<PromiseTracker>(`/api/v1/companies/${ticker}/promise-tracker`).then(setPt).catch((e) => setTabErr(e.message));
    if (tab === "sources" && !docs) api<DocRow[]>(`/api/v1/companies/${ticker}/documents`).then(setDocs).catch((e) => setTabErr(e.message));
  }, [tab, company, ticker, fin, pt, docs]);

  const go = (t: string) => { const s = t.trim().toUpperCase(); if (s) router.push(`/research?ticker=${encodeURIComponent(s)}`); };
  const newChat = () => {
    setThread([]); setQuestion("");
    if (filingId || initialQ) router.replace(`/research?ticker=${ticker}${backTo ? `&from=${encodeURIComponent(backTo)}` : ""}`);
    setTimeout(() => inputRef.current?.focus(), 0);
  };
  const suggested = company?.is_financial ? SUGGESTED_LENDER : SUGGESTED_GENERAL;
  const asked = new Set(thread.filter((t) => t.role === "user").map((t) => t.content));

  return (
    <div className="mx-auto max-w-3xl">
      {backTo && <Link href={backTo} className="mb-2 inline-flex min-h-[40px] items-center gap-1 text-[13px] text-info" data-testid="back-to-briefing">← Back to briefing</Link>}
      <div className="flex items-end justify-between gap-3">
        <h1 className="text-[30px] font-bold tracking-tight">Research</h1>
      </div>
      <p className="mt-1 text-[13.5px] text-ink-2">Answers draw only on the company&apos;s filings, annual reports, call transcripts and standardised results. Cited passages open their page; figures from standardised tables are marked as secondary.</p>
      <form className="mt-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); go(input); }} role="search">
        <input value={input} onChange={(e) => setInput(e.target.value.toUpperCase())} placeholder="Search an NSE symbol, e.g. ASIANPAINT" aria-label="NSE symbol"
          className="num min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[14px] uppercase outline-none placeholder:normal-case focus:border-accent" />
        <button className="btn btn-secondary" disabled={!input.trim()}>Open</button>
      </form>
      {!ticker && (
        <>
          <div className="mt-3 flex flex-wrap gap-1.5">{DEMOS.map((d) => <button key={d} onClick={() => go(d)} className="min-h-[36px] rounded-full border border-line px-3.5 text-[13px] text-ink-2 hover:text-ink">{d}</button>)}</div>
          <div className="mt-8"><EmptyState title="Choose a company to research" body="Pick one above, type any NSE symbol (companies outside your holdings are looked up live), or tap Investigate on a Briefing card to arrive with that filing in context." /></div>
        </>
      )}
      {indexing && (
        <div className="card mt-6 p-4 text-[13px] text-ink-2" role="status" aria-live="polite">
          <p className="font-medium text-ink">Looking up {ticker} for the first time…</p>
          <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-[12.5px] text-ink-3">
            <li>Find the company in the document index</li><li>Load filed quarterly and annual results</li><li>Index the latest call transcripts and annual report</li>
          </ol>
          <p className="mt-2 text-[12px] text-ink-3">This usually takes 30–60 seconds; afterwards it is cached.</p>
          <Skeleton className="mt-3 h-2" />
        </div>
      )}
      {cErr && (
        <div className="mt-6">
          {cErr.notFound ? (
            <EmptyState title={`FINN couldn't find ${ticker}`} body={`No company with the NSE symbol ${ticker} is listed in the document index. Check the symbol (for example HDFCBANK, not HDFC Bank). FINN answers only from a company's own documents, so it can't research a company it can't find there.`} />
          ) : <ErrorState message={cErr.msg} retry={() => loadCompany(ticker)} />}
        </div>
      )}
      {ticker && !company && !cErr && !indexing && <Skeleton className="mt-6 h-24" />}

      {company && (
        <>
          <section className="card mt-5 p-4" aria-label="Company">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-surface-3 text-[13px] font-bold" aria-hidden>{company.ticker.slice(0, 2)}</span>
                <div className="min-w-0">
                <p className="text-[12px] text-ink-3">{company.ticker} · {company.sector || "NSE"}{company.in_portfolio ? " · in your holdings" : " · not in your holdings"}{company.is_financial ? " · lender" : ""}</p>
                <h2 className="mt-0.5 text-[21px] font-bold leading-tight tracking-tight">{company.company_name}</h2>
                </div>
              </div>
              {!company.market && <p className="shrink-0 pl-14 text-[12px] text-ink-3 sm:pl-0 sm:text-right">No end-of-day price<br />loaded for this company</p>}
              {company.market && (
                <div className="shrink-0 pl-14 sm:pl-0 sm:text-right">
                  <p className="num text-[20px] font-semibold">{fmtPrice(company.market.close)}</p>
                  <p className="num text-[12px] text-ink-3">
                    <span className={tone(company.market.return_pct.value)}>{company.market.return_pct.value === null ? "move unavailable" : fmtPct(company.market.return_pct.value)}</span> · {fmtDay(company.market.trading_date)} close
                  </p>
                </div>
              )}
            </div>
            {profile?.status === "ok" && (
              <p className="mt-2 text-[13px] leading-relaxed text-ink-2" data-testid="company-profile">
                “{profile.text}” {profile.citations?.map((c, i) => <CiteChip key={i} c={c} label={`Annual report p.${c.page_number}`} />)}
              </p>
            )}
            {profile?.status === "indexing" && <p className="mt-2 text-[12px] text-ink-3">Reading the latest annual report to describe the business in the company&apos;s own words…</p>}
            {profile && ["not_found", "unavailable", "error"].includes(profile.status) && (
              <p className="mt-2 text-[12px] text-ink-3">No business description from the company&apos;s own documents yet. FINN doesn&apos;t substitute third-party summaries.</p>
            )}
          </section>

          {!filing && <LatestDevelopments ticker={company.ticker} />}
          {filing && (
            <section className="mt-3 rounded-xl border border-accent/40 bg-accent/5 p-3 text-[13px]">
              <p className="label text-accent">In context · filed {fmtTime(filing.published_at)}</p>
              <p className="mt-1 font-medium">{filing.headline}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-2">{filing.citations.slice(0, 2).map((c, i) => <CiteChip key={i} c={c} />)}
                <a className="text-[12px] text-info" href={filing.source_url} target="_blank" rel="noreferrer">Exchange PDF ↗</a></div>
            </section>
          )}

          <div className="no-scrollbar sticky top-0 z-20 -mx-4 mt-4 flex gap-1 overflow-x-auto border-b border-line bg-bg/95 px-4 backdrop-blur md:mx-0 md:px-0" role="tablist" aria-label="Research views">
            {(["ask", "evidence", "numbers", "promises", "sources"] as Tab[]).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={`min-h-[44px] shrink-0 whitespace-nowrap border-b-2 px-3 text-[13.5px] ${tab === t ? "border-accent font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink-2"}`}>
                {{ ask: "Ask", evidence: "For & against", numbers: "Financials", promises: "Promise Ledger", sources: "Sources" }[t]}
              </button>
            ))}
          </div>

          {tabErr && tab !== "ask" && <div className="mt-4"><ErrorState message={tabErr} retry={() => { setTabErr(null); setFin(null); setPt(null); setDocs(null); }} /></div>}

          {tab === "ask" && (
            <div className="mt-4 pb-40 md:pb-6">
              {aiOn === false && (
                <p className="mb-3 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[12px] leading-relaxed text-ink-3">
                  AI answers are off on this server, so answers are extractive: computed figures plus verbatim quotes, from the same sources and with the same citations.
                </p>
              )}
              {thread.length === 0 && !asking && (
                <div>
                  <p className="text-[13px] text-ink-2">Start with a question about {company.company_name}. Follow-ups keep the context of the conversation.</p>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {suggested.map((s) => (
                      <button key={s} onClick={() => ask(s, thread)} className="min-h-[44px] rounded-lg border border-line px-3 py-2 text-left text-[13px] text-ink-2 hover:border-accent hover:text-ink">{s}</button>
                    ))}
                  </div>
                </div>
              )}
              {thread.length > 0 && (
                <div className="mb-3 flex items-center justify-between gap-3">
                  <p className="text-[12px] text-ink-3">{(() => { const n = thread.filter((t) => t.role === "user" || t.error).length; return `${n} question${n === 1 ? "" : "s"} about ${company.ticker}`; })()}</p>
                  <button onClick={newChat} className="btn btn-quiet" disabled={asking}>New conversation</button>
                </div>
              )}
              {thread.some((t) => t.response) && <Legend />}
              <div className="space-y-6" aria-live="polite">
                {thread.map((t, i) => t.role === "user" ? (
                  <div key={i} className="flex justify-end"><p className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-3.5 py-2 text-[14px] text-bg">{t.content}</p></div>
                ) : t.error ? (
                  <div key={i} className="space-y-2">
                    <div className="flex justify-end"><p className="max-w-[85%] rounded-2xl rounded-br-md bg-ink/80 px-3.5 py-2 text-[14px] text-bg">{t.question}</p></div>
                    <ErrorState message={`The answer couldn't be completed. ${/[.?!]$/.test(t.error || "") ? t.error : `${t.error}.`} Your question is still in the box below.`} retry={() => ask(t.question || "", thread.slice(0, i))} />
                  </div>
                ) : (
                  <AnswerBlock key={i} t={t} onFollowUp={(q) => ask(q, thread)} />
                ))}
                {asking && (
                  <div className="space-y-2" role="status">
                    <p className="text-[12.5px] text-ink-3">Reading filings and documents, running calculations…</p>
                    <Skeleton className="h-4 w-3/4" /><Skeleton className="h-4" /><Skeleton className="h-16" />
                  </div>
                )}
                {thread.length > 0 && !asking && (
                  <div className="no-scrollbar -mx-4 flex gap-1.5 overflow-x-auto px-4 md:mx-0 md:flex-wrap md:px-0">
                    {suggested.filter((s) => !asked.has(s)).slice(0, 3).map((s) => (
                      <button key={s} onClick={() => ask(s, thread)} className="min-h-[36px] shrink-0 rounded-full border border-line px-3 text-[12.5px] text-ink-2 hover:text-ink">{s}</button>
                    ))}
                  </div>
                )}
                <div ref={endRef} />
              </div>
              <form onSubmit={(e) => { e.preventDefault(); ask(question, thread); }}
                className="composer-dock fixed inset-x-0 bottom-[calc(57px+env(safe-area-inset-bottom))] z-20 border-t border-line bg-bg/95 px-4 py-2.5 backdrop-blur md:sticky md:bottom-0 md:mx-0 md:mt-5 md:rounded-xl md:border md:px-3">
                <div className="mx-auto max-w-3xl">
                  <p className="mb-1.5 truncate text-[11.5px] text-ink-3">Asking about <span className="font-medium text-ink-2">{company.company_name}</span>{filing ? " · with the filing in context" : ""}</p>
                  <div className="flex gap-2">
                    <input ref={inputRef} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={thread.length ? "Ask a follow-up…" : "Ask about results, guidance, risks, filings…"}
                      aria-label={`Question about ${company.company_name}`} enterKeyHint="send"
                      className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[15px] outline-none focus:border-accent" />
                    <button disabled={asking || question.trim().length < 2} className="btn btn-primary">{asking ? "Working…" : "Ask"}</button>
                  </div>
                </div>
              </form>
            </div>
          )}
          {tab === "evidence" && <div className="mt-4"><Hypotheses ticker={company.ticker} /></div>}
          {tab === "numbers" && !tabErr && <div className="mt-4">{fin ? <Numbers f={fin} /> : <Skeleton className="h-64" />}</div>}
          {tab === "promises" && !tabErr && <div className="mt-4">{pt ? <Promises data={pt} /> : <Skeleton className="h-64" />}</div>}
          {tab === "sources" && !tabErr && (
            <div className="mt-4">
              {!docs ? <Skeleton className="h-40" /> : docs.length === 0 ? (
                <EmptyState title="No documents listed yet" body="The document index returned nothing for this company. Answers will rely on filed-results tables only." />
              ) : (
                <ul className="card divide-y divide-line">
                  {docs.filter((d) => d.document_type !== "presentation").slice(0, 30).map((d) => (
                    <li key={d.id} className="flex items-center gap-3 px-4 py-2.5 text-[12.5px]">
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] ${d.status === "indexed" ? "bg-up/15 text-up" : d.status === "failed" ? "bg-down/15 text-down" : "bg-surface-2 text-ink-3"}`}>
                        {d.status === "indexed" ? "Searchable" : d.status === "failed" ? "Failed" : "Listed"}
                      </span>
                      <a href={d.source_url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-ink-2 hover:text-ink">{d.title}</a>
                      <span className="num shrink-0 text-[11.5px] text-ink-3">{d.status === "indexed" && d.pages ? `${d.pages} pp` : d.publication_date ?? ""}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-[12px] leading-relaxed text-ink-3">Searchable = text extracted with page references, so answers can cite it. Listed = linked to the original exchange or company PDF and indexed when a question needs it. “Failed” documents could not be read (often scanned images) and are never cited.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function ResearchPage() {
  return <Suspense fallback={<Skeleton className="h-40" />}><ResearchInner /></Suspense>;
}
