"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Citation, Claim, DocClaim } from "@/lib/types";
import { fmtCr, fmtTime } from "@/lib/format";
import { useEvidence } from "./EvidenceSheet";
import { ErrorState, Skeleton } from "./ui";

/* Competing Hypotheses: Choose question → Review hypotheses → Compare evidence → Inspect sources → Save → Revisit. */

type Rel = "supports" | "challenges" | "does_not_distinguish" | "context";
interface Hyp { id: string; text: string; stance: "temporary_wc" | "persistent" | "custom"; origin: "suggested" | "user" }
interface InFact { company: string; metric: string; label: string; value: number; unit: string; period: string; period_type: string; basis: string | null;
  source: { kind: string; provider?: string; url?: string | null }; display: string }
interface Item { id: string; kind: "calculation" | "fact" | "document"; statement: string; explanation: string; relations: Record<string, Rel>;
  limitations: string[]; classified_by?: string | null; period_mismatch?: boolean; document?: DocClaim; facts?: InFact[];
  calculation?: { label: string; value: number; unit: string; period: string; basis: string | null; formula: string; expression: string; inputs: InFact[]; display: string } }
interface Result {
  status: string; reason?: string; ticker: string; company: string; question: string; hypotheses: Hyp[];
  premise?: { weaker: boolean; latest: string; latest_pct: number; base_years: string[]; base_median_pct: number; gap_pp: number } | null;
  basis?: string | null; items: Item[]; missing: { what: string; why: string }[]; next_questions: string[];
  table: { period: string; cfo: number; operating_profit: number; conversion_pct: number | null; working_capital_days: number | null }[];
  summary: { state: string; text: string; by_hypothesis: Record<string, Record<string, string[]>> };
  retrieval?: { retrieved?: number; shown?: number; classified_now?: number; discarded?: number; llm?: boolean };
  // saved investigations
  id?: number; current_version?: number; reviewed_version?: number; changed_since_review?: boolean; version_created_at?: string | null;
  last_checked_at?: string | null; changes?: { added: string[]; removed: string[]; changed: string[]; state: { from: string; to: string } | null } | null;
  duplicate_of?: number; evidence_changed?: boolean;
}
interface Saved { id: number; question: string; status: string; current_version: number; changed_since_review: boolean; updated_at: string }

const EXAMPLE = "Is weaker cash conversion temporary working-capital expansion or persistent deterioration?";
const STANCE: Record<string, string> = { temporary_wc: "tested as a temporary working-capital swing", persistent: "tested as a lasting change", custom: "tested against document passages only" };
const STATE: Record<string, { title: string; cls: string }> = {
  conflicting: { title: "The evidence conflicts", cls: "border-accent/40 bg-accent/5" },
  insufficient: { title: "Not enough evidence to settle this", cls: "border-line bg-surface-2/60" },
  one_sided: { title: "The evidence found points one way", cls: "border-info/40 bg-info/5" },
  premise_not_supported: { title: "The premise isn't supported by the figures", cls: "border-line bg-surface-2/60" },
  incompatible: { title: "FINN can't compare this", cls: "border-down/40 bg-down/5" },
  unsupported_question: { title: "Not a question this tool covers yet", cls: "border-line bg-surface-2/60" },
};
const REL: Record<Rel, { label: string; cls: string }> = {
  supports: { label: "Supports", cls: "text-up border-up/40" }, challenges: { label: "Challenges", cls: "text-down border-down/40" },
  does_not_distinguish: { label: "Doesn't distinguish", cls: "text-ink-3 border-line" }, context: { label: "Context", cls: "text-ink-3 border-line" },
};

const DOC_TYPE: Record<string, string> = { concall_transcript: "earnings call", annual_report: "annual report", presentation: "investor presentation", exchange_filing: "exchange filing" };

const docCitation = (d: DocClaim): Citation => ({ kind: "document", ticker: d.company, document_id: d.document_id, document_title: d.document_title,
  document_type: d.document_type, chunk_id: d.chunk_id, page_number: d.page, relevant_excerpt: d.passage, source_url: d.source_url,
  publication_date: d.published, reporting_period: d.reporting_period, speaker: d.speaker });

/** Structured evidence → the Evidence Lens claim view (the figure, its formula, and every input with period and basis). */
function toClaims(it: Item): { ids: string[]; claims: Record<string, Claim> } {
  const claims: Record<string, Claim> = {};
  const ins = it.calculation?.inputs ?? it.facts ?? [];
  const inputIds = ins.map((f, i) => {
    const id = `F${i + 1}`;
    claims[id] = { id, kind: "fact", company: f.company, metric: f.metric, label: f.label, measure: f.metric, value: f.value, unit: f.unit, period: f.period,
      period_type: f.period_type, basis: f.basis, base_period: null, formula: null, expression: null, inputs: [], display: f.display,
      source: { kind: "standardised", title: `Standardised annual statements (${f.source.provider ?? "data source"})`, url: f.source.url ?? null } };
    return id;
  });
  if (!it.calculation) return { ids: inputIds, claims };
  const c = it.calculation;
  claims.C1 = { id: "C1", kind: "calc", company: ins[0]?.company ?? "", metric: c.label, label: c.label, measure: c.label, value: c.value, unit: c.unit,
    period: c.period, period_type: "annual", basis: c.basis, base_period: null, formula: c.formula, expression: c.expression, inputs: inputIds,
    display: c.display, source: { kind: "calculation" } };
  return { ids: ["C1"], claims };
}

function EvidenceRow({ it, hyps, against }: { it: Item; hyps: Hyp[]; against?: string }) {
  const open = useEvidence();
  const src = () => {
    if (it.document) return open(docCitation(it.document));
    const { ids, claims } = toClaims(it);
    open({ kind: "claims", claim_ids: ids, claims, document_title: it.calculation?.label ?? "Figures" });
  };
  return (
    <li className="card p-3" data-testid={`ev-${it.kind}`}>
      <div className="flex flex-wrap gap-1">{hyps.map((h, i) => {
        const r = REL[it.relations[h.id] ?? "context"];
        return <span key={h.id} className={`rounded border px-1 text-[10.5px] font-medium uppercase tracking-wide ${r.cls}`}>H{i + 1} · {r.label}</span>;
      })}<span className="ml-auto text-[11px] text-ink-3">{it.kind === "document" ? (it.classified_by === "ai" ? "Passage · AI-classified" : "Passage · not classified") : "Calculated from filed figures"}</span></div>
      <p className="mt-1.5 text-[13.5px] text-ink">{it.kind === "document" ? <>“{it.statement}”</> : it.statement}</p>
      {it.document && <p className="text-[11.5px] text-ink-3">{it.document.document_title} · p.{it.document.page}{it.document.speaker ? ` · ${it.document.speaker}` : ""}</p>}
      {it.document && against && (
        <p className="mt-1 rounded-md bg-surface-2/70 px-2 py-1 text-[11.5px] text-ink-2" data-testid="ev-compare">
          <span className="text-ink-3">Passage:</span> {DOC_TYPE[it.document.document_type] ?? it.document.document_type}{it.document.reporting_period ? `, ${it.document.reporting_period}` : ""}
          {" · "}<span className="text-ink-3">Computed figures:</span> {against}</p>
      )}
      {it.period_mismatch && <p className="mt-1 text-[12px] text-accent">Quarterly or half-year statement — not directly comparable with the annual figures above.</p>}
      <p className="mt-1 text-[12.5px] text-ink-2"><span className="text-ink-3">Why it matters here:</span> {it.explanation}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-3">
        <button className="text-[12.5px] text-info" onClick={src}>{it.document ? "Open passage" : "Open figures & inputs"}</button>

      </div>
      {it.limitations.length > 0 && <ul className="mt-1 space-y-0.5 text-[12px] text-ink-3" aria-label="Limitations">{it.limitations.map((l) => <li key={l}><span className="font-medium">Limitation:</span> {l}</li>)}</ul>}
    </li>
  );
}

function ResultView({ r }: { r: Result }) {
  const st = STATE[r.summary.state] ?? STATE.insufficient;
  const [view, setView] = useState<string>("all");
  if (r.status !== "ok") return <div className={`rounded-xl border p-3 ${st.cls}`} role="status"><p className="font-medium">{st.title}</p><p className="mt-1 text-[13px] text-ink-2">{r.reason}</p></div>;
  const distinguishing = r.items.filter((i) => Object.values(i.relations).some((x) => x === "supports" || x === "challenges"));
  const rest = r.items.filter((i) => !distinguishing.includes(i));
  // Period and basis of the computed evidence, from structured fields only (never parsed from passage text).
  const computedScope = [r.table.length ? `${r.table[0].period}–${r.table[r.table.length - 1].period} (full years)` : null, r.basis].filter(Boolean).join(", ") || "period not recorded";
  const shown = view === "all" ? distinguishing : distinguishing.filter((i) => i.relations[view] === "supports" || i.relations[view] === "challenges");
  return (
    <div className="space-y-4" data-testid="hyp-result">
      <div className={`rounded-xl border p-3 ${st.cls}`} role="status" data-testid="hyp-state">
        <p className="font-medium">{st.title}</p><p className="mt-0.5 text-[13px] text-ink-2">{r.summary.text}</p>
        {r.premise && <p className="mt-1 text-[12px] text-ink-3">{r.premise.latest} cash conversion {r.premise.latest_pct}% vs {r.premise.base_median_pct}% median for {r.premise.base_years[0]}–{r.premise.base_years[r.premise.base_years.length - 1]} ({r.premise.gap_pp > 0 ? "+" : ""}{r.premise.gap_pp} pp) · {r.basis ?? "basis unknown"}</p>}
      </div>
      <p className="text-[11.5px] text-ink-3">Counts show how much evidence was found, not how strong it is: one filed figure can outweigh several passages, and computed figures and passages often cover different periods or parts of the business.</p>
      <div className="grid gap-2 sm:grid-cols-2" aria-label="Count of evidence per hypothesis">
        {r.hypotheses.map((h, i) => (
          <div key={h.id} className="rounded-lg border border-line p-2.5 text-[12.5px]">
            <p className="font-medium text-ink">H{i + 1}. {h.text}</p>
            {(["computed", "passage"] as const).map((g) => {
              const its = r.items.filter((x) => (x.kind === "document") === (g === "passage"));
              const n = (rel: Rel) => its.filter((x) => x.relations[h.id] === rel).length;
              return <p key={g} className="mt-1 text-ink-3">{g === "computed" ? "Computed figures" : "Passages"}: <span className="text-up">{n("supports")} support</span> · <span className="text-down">{n("challenges")} challenge</span></p>;
            })}
          </div>
        ))}
      </div>
      {distinguishing.length > 0 && (
        <section aria-labelledby="ev-h" className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2"><h3 id="ev-h" className="text-[15px] font-semibold">Evidence for and against</h3>
            <div className="flex gap-1" role="group" aria-label="Filter by hypothesis">
              {["all", ...r.hypotheses.map((h) => h.id)].map((k, i) => <button key={k} aria-pressed={view === k} onClick={() => setView(k)}
                className={`min-h-[32px] rounded-full px-2.5 text-[12px] ${view === k ? "bg-ink text-bg" : "border border-line text-ink-2"}`}>{k === "all" ? "All" : `H${i}`}</button>)}</div></div>
          {shown.some((i) => i.kind !== "document") && <>
            <h4 className="label">Computed from annual statements · {computedScope}</h4>
            <ul className="space-y-2">{shown.filter((i) => i.kind !== "document").map((it) => <EvidenceRow key={it.id} it={it} hyps={r.hypotheses} />)}</ul></>}
          {shown.some((i) => i.kind === "document") && <>
            <h4 className="label mt-3">Passages from the company&apos;s documents</h4>
            <p className="text-[11.5px] text-ink-3" data-testid="scope-note">Not directly comparable with the figures above unless period and scope match. FINN records each passage&apos;s document and date, but not which part of the business it covers (for example consolidated vs excluding a segment), so read the quote.</p>
            <ul className="space-y-2">{shown.filter((i) => i.kind === "document").map((it) => <EvidenceRow key={it.id} it={it} hyps={r.hypotheses} against={computedScope} />)}</ul></>}
        </section>
      )}
      {r.table.length > 0 && (
        <details className="rounded-lg border border-line" open={false}>
          <summary className="cursor-pointer px-3 py-2 text-[13px] text-ink-2">Figures by year (₹ crore, {r.basis ?? "basis unknown"})</summary>
          <div className="no-scrollbar overflow-x-auto"><table className="w-full min-w-[420px] text-[12px]"><caption className="sr-only">Cash from operations, operating profit, cash conversion and working-capital days by financial year</caption>
            <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-ink-3"><tr><th scope="col" className="px-2.5 py-1.5 text-left font-medium">Year</th><th scope="col" className="px-2 text-right font-medium">CFO</th><th scope="col" className="px-2 text-right font-medium">Op. profit</th><th scope="col" className="px-2 text-right font-medium">CFO ÷ op. profit</th><th scope="col" className="px-2.5 text-right font-medium">WC days</th></tr></thead>
            <tbody>{r.table.map((t) => <tr key={t.period} className="border-t border-line"><th scope="row" className="px-2.5 py-1.5 text-left font-normal">{t.period}</th>
              <td className="num px-2 text-right">{fmtCr(t.cfo).replace(" cr", "")}</td><td className="num px-2 text-right">{fmtCr(t.operating_profit).replace(" cr", "")}</td>
              <td className="num px-2 text-right">{t.conversion_pct === null ? "n/m" : `${t.conversion_pct}%`}</td><td className="num px-2.5 text-right">{t.working_capital_days ?? "—"}</td></tr>)}</tbody></table></div>
        </details>
      )}
      {r.missing.length > 0 && (
        <section aria-labelledby="miss-h"><h3 id="miss-h" className="text-[14px] font-semibold">Missing evidence</h3>
          <ul className="mt-1 space-y-1 text-[12.5px] text-ink-2">{r.missing.map((m) => <li key={m.what}><span className="text-ink">{m.what}</span> — {m.why}</li>)}</ul></section>
      )}
      {r.next_questions.length > 0 && (
        <section aria-labelledby="nq-h"><h3 id="nq-h" className="text-[14px] font-semibold">Next questions</h3>
          <ul className="mt-1 space-y-1 text-[12.5px] text-ink-2">{r.next_questions.map((q) => <li key={q}>• {q}</li>)}</ul></section>
      )}
      {rest.length > 0 && (
        <details><summary className="cursor-pointer text-[13px] text-ink-2">{rest.length} item{rest.length === 1 ? "" : "s"} that don&apos;t distinguish or are context</summary>
          <ul className="mt-2 space-y-2">{rest.map((it) => <EvidenceRow key={it.id} it={it} hyps={r.hypotheses} against={computedScope} />)}</ul></details>
      )}
      {r.retrieval && (
        <p className="text-[11.5px] text-ink-3">Passages: {r.retrieval.shown ?? 0} shown of {r.retrieval.retrieved ?? 0} retrieved{r.retrieval.llm ? ` · ${r.retrieval.classified_now ?? 0} classified by AI now, ${r.retrieval.discarded ?? 0} judgements discarded (quote not found verbatim)` : " · no AI classification this run"}. No probabilities or verdicts are produced.</p>
      )}
    </div>
  );
}

export function Hypotheses({ ticker }: { ticker: string }) {
  const [question, setQuestion] = useState(EXAMPLE);
  const [hyps, setHyps] = useState<Hyp[] | null>(null);
  const [refusal, setRefusal] = useState<{ status: string; reason: string } | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [saved, setSaved] = useState<Saved[] | null>(null);
  const loadSaved = useCallback(() => { api<Saved[]>(`/api/v1/investigations?ticker=${ticker}`).then(setSaved).catch(() => setSaved([])); }, [ticker]);
  useEffect(loadSaved, [loadSaved]);

  const suggest = async () => {
    setBusy("suggest"); setErr(null); setResult(null); setRefusal(null); setNote(null);
    try {
      const r = await api<{ refusal: { status: string; reason: string } | null; hypotheses: Hyp[] }>(`/api/v1/investigations/suggest?ticker=${ticker}&question=${encodeURIComponent(question)}`);
      setRefusal(r.refusal); setHyps(r.refusal ? null : r.hypotheses);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const compare = async () => {
    if (!hyps || busy) return;
    setBusy("compare"); setErr(null); setNote(null);
    try { setResult(await api<Result>("/api/v1/investigations/compare", { method: "POST", body: JSON.stringify({ ticker, question, hypotheses: hyps }) })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const save = async () => {
    if (!hyps || busy) return;
    setBusy("save");
    try {
      const r = await api<Result>("/api/v1/investigations", { method: "POST", body: JSON.stringify({ ticker, question, hypotheses: hyps }) });
      setNote(r.duplicate_of ? "This investigation is already saved." : "Investigation saved. FINN re-checks the evidence after each refresh and flags changes in your briefing.");
      setResult(r); loadSaved();
    } catch (e) { setErr(`Not saved: ${(e as Error).message}`); } finally { setBusy(null); }
  };
  const openSaved = async (id: number) => {
    setBusy("open"); setErr(null); setNote(null);
    try { const r = await api<Result>(`/api/v1/investigations/${id}`); setResult(r); setHyps(r.hypotheses); setQuestion(r.question); setRefusal(null); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const act = async (path: string, body?: object) => {
    if (!result?.id) return;
    setBusy(path);
    try {
      const r = await api<Result>(`/api/v1/investigations/${result.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      if (path === "refresh") { setResult(r); setNote(r.evidence_changed ? `New evidence: saved as version ${r.current_version}.` : `No change in the evidence (checked ${fmtTime(r.last_checked_at)}).`); }
      else { setNote("Changes marked as seen."); await openSaved(result.id); }
      loadSaved();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const edit = (i: number, text: string) => setHyps((hs) => hs && hs.map((h, j) => j === i ? { ...h, text, origin: "user" } : h));

  return (
    <div className="space-y-4" data-testid="hypotheses">
      <div>
        <h2 className="text-[17px] font-semibold">Evidence for and against</h2>
        <p className="text-[12.5px] text-ink-3">Compare two or three explanations against filed figures and the company&apos;s own documents. FINN lists the evidence; it does not pick a winner.</p>
        <p className="mt-1 rounded-lg bg-surface-2 px-2.5 py-1.5 text-[12px] text-ink-2" data-testid="hyp-scope">Supported today: questions about weaker <strong className="font-semibold">cash conversion or working capital</strong>, using full-year statements. Quarterly questions and lenders are declined with the reason; other questions belong in Ask.</p>
      </div>
      {saved && saved.length > 0 && !result?.id && (
        <ul className="space-y-1.5" aria-label="Saved investigations">{saved.map((s) => (
          <li key={s.id}><button className="card flex w-full items-center justify-between gap-2 p-2.5 text-left text-[13px]" onClick={() => openSaved(s.id)}>
            <span className="min-w-0"><span className="block truncate text-ink">{s.question}</span><span className="text-[11.5px] text-ink-3">v{s.current_version} · {STATE[s.status]?.title ?? s.status}</span></span>
            {s.changed_since_review && <span className="shrink-0 rounded-full bg-accent px-2 py-0.5 text-[11px] text-accent-ink">Evidence changed</span>}</button></li>))}</ul>
      )}
      {!result?.id && (
        <section className="card space-y-3 p-3" aria-label="Choose a question">
          <label className="block text-[12.5px] text-ink-3">Question
            <textarea rows={2} value={question} maxLength={400} onChange={(e) => { setQuestion(e.target.value); setHyps(null); setResult(null); setRefusal(null); }}
              className="mt-1 block w-full rounded-lg border border-line bg-bg px-3 py-2 text-[14px] text-ink" /></label>
          {!hyps && <button className="btn btn-primary" onClick={suggest} disabled={busy !== null || question.trim().length < 8}>{busy === "suggest" ? "Checking…" : "Suggest hypotheses"}</button>}
          {refusal && <ResultView r={{ status: refusal.status, reason: refusal.reason, summary: { state: refusal.status, text: refusal.reason, by_hypothesis: {} } } as Result} />}
          {hyps && (
            <div className="space-y-2" aria-label="Hypotheses">
              <p className="label">Hypotheses · edit the wording if you like</p>
              {hyps.map((h, i) => (
                <div key={h.id} className="rounded-lg border border-line p-2">
                  <label className="block text-[12px] text-ink-3">H{i + 1} {h.origin === "suggested" ? <span className="ml-1 rounded bg-surface-2 px-1 text-[10.5px] uppercase tracking-wide">Suggested by FINN · standard template</span> : <span className="ml-1 text-[11px]">your wording</span>}
                    <textarea rows={2} value={h.text} maxLength={300} onChange={(e) => edit(i, e.target.value)} className="mt-1 block w-full rounded-md border border-line bg-bg px-2 py-1.5 text-[13.5px] text-ink" /></label>
                  <p className="mt-0.5 text-[11px] text-ink-3">{STANCE[h.stance]}{hyps.length > 2 && <button className="ml-2 text-info" onClick={() => setHyps(hyps.filter((_, j) => j !== i))}>Remove</button>}</p>
                </div>
              ))}
              {hyps.length < 3 && <button className="btn btn-quiet" onClick={() => setHyps([...hyps, { id: `h${[1, 2, 3].find((n) => !hyps.some((h) => h.id === `h${n}`))}`, text: "", stance: "custom", origin: "user" }])}>Add a third hypothesis</button>}
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-primary" onClick={compare} disabled={busy !== null || hyps.some((h) => h.text.trim().length < 8)} aria-busy={busy === "compare"}>{busy === "compare" ? "Comparing evidence…" : "Compare evidence"}</button>
                {result && result.status === "ok" && <button className="btn btn-secondary" onClick={save} disabled={busy !== null}>{busy === "save" ? "Saving…" : "Save investigation"}</button>}
              </div>
              {busy === "compare" && <p className="text-[12px] text-ink-3" role="status">Computing figures and reading passages for and against each hypothesis — this can take up to half a minute.</p>}
            </div>
          )}
        </section>
      )}
      {result?.id && (
        <section className="card space-y-2 p-3" aria-label="Saved investigation">
          <p className="text-[13.5px] font-medium">{result.question}</p>
          <p className="text-[12px] text-ink-3">Version {result.current_version} · evidence from {fmtTime(result.version_created_at)} · last checked {fmtTime(result.last_checked_at)}</p>
          {result.changed_since_review && result.changes && (
            <div className="rounded-lg border border-accent/40 bg-accent/5 p-2.5 text-[12.5px]" data-testid="hyp-changes">
              <p className="font-medium">Evidence changed since you last reviewed (v{result.reviewed_version} → v{result.current_version})</p>
              {result.changes.state && <p>Overall: {STATE[result.changes.state.from]?.title ?? result.changes.state.from} → {STATE[result.changes.state.to]?.title ?? result.changes.state.to}</p>}
              {result.changes.added.map((a) => <p key={a}>+ {a}</p>)}{result.changes.removed.map((a) => <p key={a} className="text-ink-3">− {a}</p>)}
              {result.changes.changed.map((a) => <p key={a}>~ {a}</p>)}
              <button className="btn btn-secondary mt-1.5" disabled={busy !== null} onClick={() => act("reviewed", { version: result.current_version })}>Mark changes seen</button>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-quiet" onClick={() => act("refresh")} disabled={busy !== null}>{busy === "refresh" ? "Checking…" : "Check for new evidence"}</button>
            <button className="btn btn-quiet" onClick={() => { setResult(null); setHyps(null); setQuestion(EXAMPLE); }}>New investigation</button>
          </div>
        </section>
      )}
      {note && <p role="status" className="rounded-xl border border-up/30 bg-up/10 px-3 py-2 text-[13px]">{note}</p>}
      {err && <ErrorState message={err} retry={result?.id ? () => openSaved(result.id!) : hyps ? compare : suggest} />}
      {busy === "open" && <Skeleton className="h-40" />}
      {result && <ResultView r={result} />}
    </div>
  );
}
