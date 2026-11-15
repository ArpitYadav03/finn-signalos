"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { BriefingItem, Citation, DnaStep, DocClaim, MonitoringPlan, PlanBriefing, PlanDraft } from "@/lib/types";
import { fmtDate, fmtDay, fmtPct, fmtPrice, fmtTime, investigateQuestion } from "@/lib/format";
import { planBriefingHref, researchHref } from "@/lib/researchLink";
import { planSubtitle } from "@/lib/briefingView";
import { useEvidence } from "./EvidenceSheet";
import { ErrorState, Skeleton } from "./ui";

/* Investor Handover: Describe → Review the monitoring plan → Save → Monitor → Review changes → Investigate or mark reviewed. */

const EXAMPLES = [
  "Watch my holdings for guidance cuts, CFO resignations and changes to announced fundraising.",
  "Tell me if any holding falls more than 5% in a day and its volume is above 2x on the same day.",
  "Watch Infosys and TCS for guidance cuts and credit rating downgrades.",
];
const DRAFT_KEY = "finn.plan.draft";
const read = (k: string) => { try { return sessionStorage.getItem(k) || ""; } catch { return ""; } };
const write = (k: string, v: string) => { try { sessionStorage.setItem(k, v); } catch { /* storage unavailable */ } };

const docCitation = (d: DocClaim): Citation => ({ kind: "document", ticker: d.company, document_id: d.document_id, document_title: d.document_title,
  document_type: d.document_type, chunk_id: d.chunk_id, page_number: d.page, relevant_excerpt: d.passage, source_url: d.source_url,
  publication_date: d.published, reporting_period: d.reporting_period, speaker: d.speaker });

const SHORT: Record<string, string> = { guidance_cut: "Guidance cuts", fundraise_update: "Fund raises", price_move: "Price moves", volume: "Volume spikes" };
function planName(d: PlanDraft): string {
  const parts = (d.spec?.conditions ?? []).map((c) => c.kind !== "filing" ? SHORT[c.kind]
    : c.categories?.[0] === "management_change" ? `${c.roles?.join("/") ?? "Leadership"} ${c.change === "departure" ? "exits" : c.change === "appointment" ? "appointments" : "changes"}`
    : `${(c.categories ?? []).join(", ").replace(/_/g, " ")}${c.rating_action ? ` ${c.rating_action}s` : ""}`);
  const scope = d.spec?.tickers?.length ? d.spec.tickers.join(", ") : "Holdings";
  return `${scope}: ${parts.join(" · ")}`.slice(0, 120);
}

/** The plan editor: plain words in, a validated plan back for review; nothing is saved until "Save plan". */
export function PlanComposer({ editing, onSaved, onCancel }: { editing?: MonitoringPlan | null; onSaved: (p: MonitoringPlan, note: string) => void; onCancel?: () => void }) {
  const [text, setText] = useState(editing?.description ?? "");
  const [draft, setDraft] = useState<PlanDraft | null>(null);
  const [name, setName] = useState(editing?.name ?? "");
  const [busy, setBusy] = useState<"draft" | "save" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reviewRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!editing) setText(read(DRAFT_KEY)); }, [editing]);
  useEffect(() => { if (draft) reviewRef.current?.focus(); }, [draft]);

  const review = async (clarification?: number) => {
    if (busy || text.trim().length < 3) return;
    setBusy("draft"); setErr(null);
    try {
      const d = await api<PlanDraft>("/api/v1/plans/draft", { method: "POST", body: JSON.stringify({ text, clarification }) });
      setDraft(d);
      if (!name && d.spec) setName(planName(d));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const save = async () => {
    if (!draft?.spec || busy) return;
    setBusy("save"); setErr(null);
    try {
      const body = JSON.stringify({ name: name.trim() || "My plan", description: text.trim(), spec: draft.spec });
      const p = await api<MonitoringPlan>(editing ? `/api/v1/plans/${editing.id}` : "/api/v1/plans", { method: editing ? "PUT" : "POST", body });
      write(DRAFT_KEY, "");
      onSaved(p, p.duplicate_of ? "You already have this plan — nothing new was saved." :
        editing ? `Saved as version ${p.version}. Earlier evaluations keep the version they used.` : "Plan saved. FINN evaluates it after each data refresh.");
      setDraft(null); setText(""); setName("");
    } catch (e) { setErr(`Not saved: ${(e as Error).message}`); } finally { setBusy(null); }
  };

  return (
    <section className="card space-y-3 p-4" aria-labelledby="plan-h" data-testid="plan-composer">
      <div>
        <h2 id="plan-h" className="text-[17px] font-semibold">{editing ? `Edit “${editing.name}”` : "What should FINN watch?"}</h2>
        <p className="text-[12.5px] text-ink-3">Describe it in your own words. You review the plan before anything is saved.</p>
      </div>
      <label className="block">
        <span className="sr-only">Describe what FINN should watch</span>
        <textarea value={text} rows={3} maxLength={600} enterKeyHint="done"
          onChange={(e) => { setText(e.target.value); setDraft(null); if (!editing) write(DRAFT_KEY, e.target.value); }}
          placeholder="e.g. guidance cuts and CFO changes in my holdings" className="block w-full resize-y rounded-lg border border-line bg-bg px-3 py-2 text-[14px] text-ink" />
      </label>
      {!text && !editing && (
        <div className="flex flex-wrap gap-1.5" aria-label="Examples">
          {EXAMPLES.map((x) => <button key={x} className="min-h-[36px] rounded-full border border-line px-3 text-left text-[12.5px] text-ink-2"
            onClick={() => { setText(x); write(DRAFT_KEY, x); }}>{x.length > 58 ? `${x.slice(0, 56)}…` : x}</button>)}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary" onClick={() => review()} disabled={busy !== null || text.trim().length < 3} aria-busy={busy === "draft"}>
          {busy === "draft" ? "Reading…" : "Review plan"}</button>
        {onCancel && <button className="btn btn-quiet" onClick={onCancel}>Cancel</button>}
      </div>
      {err && <p role="alert" className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-[13px]">{err}</p>}
      {draft && (
        <div ref={reviewRef} tabIndex={-1} className="space-y-3 rounded-xl border border-line bg-surface-2/50 p-3 outline-none" data-testid="plan-review" aria-live="polite">
          {draft.clarification && (
            <div className="rounded-lg border border-accent/40 bg-accent/5 p-3">
              <p className="text-[13.5px] font-medium">{draft.clarification.question}</p>
              <p className="text-[12px] text-ink-3">{draft.clarification.why}</p>
              <div className="mt-2 flex gap-2">{draft.clarification.options.map((o) =>
                <button key={o} className="btn btn-secondary min-w-[64px]" onClick={() => review(o)} disabled={busy !== null}>{o}%</button>)}</div>
            </div>
          )}
          {draft.unsupported.length > 0 && (
            <p className="rounded-lg border border-accent/40 bg-accent/5 px-3 py-2 text-[13px]" role="status">
              <span className="font-medium">Not included:</span> {draft.unsupported.join("; ")}. FINN can&apos;t monitor these, so they are left out of the plan.</p>
          )}
          {!draft.spec && !draft.clarification && <p className="text-[13px] text-ink-2">{draft.explanation} Try naming an event (results, guidance cut, CFO change, fund raise, rating downgrade) or a price/volume threshold.</p>}
          {draft.preview && (
            <>
              <dl className="grid gap-2 text-[13px] sm:grid-cols-[110px_1fr]">
                <dt className="text-ink-3">Companies</dt><dd className="text-ink">{draft.preview.scope}</dd>
                <dt className="text-ink-3">Report when</dt>
                <dd><ul className="space-y-1">{draft.preview.conditions.map((c, i) =>
                  <li key={i} className="flex gap-2 text-ink"><span className="text-ink-3">{i > 0 ? (draft.spec?.logical_operator === "AND" ? "and" : "or") : "•"}</span>{c}</li>)}</ul></dd>
                {draft.preview.combine && <><dt className="text-ink-3">Combined</dt><dd className="text-ink-2">{draft.preview.combine}</dd></>}
                <dt className="text-ink-3">Checked</dt><dd className="text-ink-2">{draft.preview.frequency}</dd>
              </dl>
              <label className="block text-[12.5px] text-ink-3">Plan name
                <input value={name} maxLength={160} onChange={(e) => setName(e.target.value)} className="mt-1 block w-full rounded-lg border border-line bg-bg px-3 py-2 text-[14px] text-ink" /></label>
              <button className="btn btn-primary w-full sm:w-auto" onClick={save} disabled={busy !== null || !draft.spec} aria-busy={busy === "save"}>
                {busy === "save" ? "Saving…" : editing ? "Save changes" : "Save plan"}</button>
            </>
          )}
        </div>
      )}
    </section>
  );
}

/** Saved plans: pause/resume, edit, and the link to its briefing. */
export function PlanList({ plans, onChange, onEdit, onReplay }: { plans: MonitoringPlan[]; onChange: () => void; onEdit: (p: MonitoringPlan) => void;
  onReplay?: (p: MonitoringPlan) => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const toggle = async (p: MonitoringPlan) => {
    setBusy(p.id);
    try { await api(`/api/v1/plans/${p.id}/${p.enabled ? "pause" : "resume"}`, { method: "POST" });
      setNote(p.enabled ? `“${p.name}” paused. It won't be evaluated until you resume it.` : `“${p.name}” resumed.`); onChange(); }
    catch (e) { setNote(`Not changed: ${(e as Error).message}`); } finally { setBusy(null); }
  };
  if (!plans.length) return null;
  return (
    <section className="space-y-2" aria-label="Your monitoring plans" data-testid="plan-list">
      <h2 className="text-[15px] font-semibold">Your plans</h2>
      {note && <p role="status" className="text-[12.5px] text-ink-2">{note}</p>}
      {plans.map((p) => (
        <article key={p.id} className="card p-3" data-testid={`plan-${p.id}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="text-[14px] font-semibold">{p.name} <span className="text-[11.5px] font-normal text-ink-3">v{p.version}</span></h3>
              <p className="text-[12.5px] text-ink-3">{planSubtitle(p)} · {p.enabled ? `last evaluated ${p.last_evaluated_at ? fmtTime(p.last_evaluated_at) : "not yet"}` : "Paused"}</p>
            </div>
            <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${p.enabled ? "bg-up/15 text-up" : "bg-surface-2 text-ink-3"}`}>{p.enabled ? "Active" : "Paused"}</span>
          </div>
          <ul className="mt-2 space-y-0.5 text-[12.5px] text-ink-2">{p.preview.conditions.map((c, i) => <li key={i}>• {c}</li>)}</ul>
          <div className="mt-2 flex flex-wrap gap-2">
            <Link href={`/?plan=${p.id}`} className="btn btn-secondary">Changes since your last review</Link>
            <button className="btn btn-quiet" onClick={() => onEdit(p)}>Edit</button>
            <button className="btn btn-quiet" onClick={() => toggle(p)} disabled={busy === p.id}>{p.enabled ? "Pause" : "Resume"}</button>
            {onReplay && <button className="btn btn-quiet" onClick={() => onReplay(p)}>Test this plan on past data</button>}
          </div>
        </article>
      ))}
    </section>
  );
}

function Step({ d, inline = false }: { d: DnaStep; inline?: boolean }) {
  const open = useEvidence();
  const Tag = inline ? "span" : "li";
  return (
    <Tag className={`${inline ? "inline-flex" : "flex"} flex-wrap items-baseline gap-x-2 text-[12.5px]`}>
      <span className="rounded border border-line px-1 text-[10.5px] uppercase tracking-wide text-ink-3">{d.role}</span>
      <span className="text-ink-2">{d.added}</span>
      <span className="num text-[11.5px] text-ink-3">{fmtTime(d.published_at)}</span>
      {d.citation && <button className="text-[12px] text-info" onClick={() => open(d.citation!)}>View evidence</button>}
    </Tag>
  );
}

function Item({ it, onReview, busy, planId }: { it: BriefingItem; onReview?: (it: BriefingItem) => void; busy: boolean; planId: number }) {
  const open = useEvidence();
  const title = it.card?.headline ?? (it.kind === "investigation" ? `Evidence changed: ${it.question}` : it.guidance_cut ? `${it.company ?? it.ticker}: ${it.guidance_cut.metric.replace(/_/g, " ")} guidance lowered` :
    it.market ? `${it.ticker}: ${it.market.unit === "%" ? fmtPct(it.market.value) : `${it.market.value.toFixed(1)}× average volume`} on ${fmtDay(it.market.session)}` : it.ticker);
  return (
    <article className="card scroll-mt-20 p-3" id={`item-${it.id}`} data-testid={`item-${it.kind}`}>
      <p className="text-[11.5px] text-ink-3"><span className="font-semibold text-ink-2">{it.ticker}</span>{it.latest_published_at && <> · {it.kind === "event" ? fmtTime(it.latest_published_at) : it.kind === "market" ? fmtDay(it.latest_published_at) : fmtDate(it.latest_published_at)}</>}</p>
      <h4 className="mt-0.5 text-[14px] font-semibold leading-snug">{title}</h4>
      {it.matched && <p className="mt-1 text-[12.5px] text-ink-2"><span className="text-ink-3">Why it&apos;s here:</span> {it.matched.join("; ")}</p>}
      {it.new_filings && it.new_filings.length > 0 && (
        <div className="mt-2"><p className="label">Not in the version you reviewed{it.reviewed_at ? ` (reviewed ${fmtTime(it.reviewed_at)})` : ""}</p>
          <ul className="mt-1 space-y-1">{it.new_filings.map((d) => <Step key={d.id} d={d} />)}</ul>
          {it.repeats && it.repeats.length > 0 && <p className="mt-1 text-[12px] text-ink-3">Also filed: {it.repeats.length} repeat of an earlier disclosure (nothing new).</p>}</div>
      )}
      {!it.new_filings && it.event_dna && (
        <ul className="mt-2 space-y-1" aria-label="Filings in this event">{it.event_dna.map((d) => <Step key={d.id} d={d} />)}</ul>
      )}
      {it.guidance_cut && (
        <div className="mt-2 space-y-1.5 text-[12.5px]">
          <table className="w-full text-[12.5px]"><caption className="sr-only">Guidance before and after</caption>
            <thead className="text-[11px] uppercase tracking-wider text-ink-3"><tr><th scope="col" className="text-left font-medium">{it.guidance_cut.period} · {it.guidance_cut.basis.replace(/_/g, " ")}</th><th scope="col" className="text-right font-medium">Target</th><th scope="col" className="text-right font-medium">Stated</th></tr></thead>
            <tbody><tr className="border-t border-line"><th scope="row" className="py-1 text-left font-normal text-ink-3">Before</th><td className="num text-right">{it.guidance_cut.previous}</td><td className="num text-right text-ink-3">{fmtDate(it.guidance_cut.previous_date)}</td></tr>
              <tr className="border-t border-line"><th scope="row" className="py-1 text-left font-normal">Now</th><td className="num text-right font-semibold">{it.guidance_cut.current}</td><td className="num text-right text-ink-3">{fmtDate(it.guidance_cut.date)}</td></tr></tbody></table>
          {it.evidence?.map((e) => (
            <p key={e.label} className="text-ink-2"><span className="text-ink-3">{e.label}:</span> {e.document ? <>“{e.document.passage}” <button className="text-info" onClick={() => open(docCitation(e.document!))}>p.{e.document.page}</button></> : e.limitation}</p>
          ))}
          <p className="text-[11.5px] text-ink-3">Rule: {it.guidance_cut.rule}.</p>
        </div>
      )}
      {it.market && <p className="mt-1 text-[12.5px] text-ink-2">Close {fmtPrice(it.market.close)}{it.market.previous_close ? ` vs ${fmtPrice(it.market.previous_close)}` : ""} · end-of-day ({it.market.source})</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        {it.card && <Link className="btn btn-secondary" href={researchHref({ ticker: it.ticker, filingId: it.card.id,
          question: investigateQuestion(it.card.category, it.card.headline), from: planBriefingHref(planId, it.id) })}>Investigate</Link>}
        {it.kind === "investigation" && <Link className="btn btn-secondary" href={researchHref({ ticker: it.ticker, tab: "evidence", from: planBriefingHref(planId, it.id) })}>Open investigation (v{it.versions?.reviewed} → v{it.versions?.current})</Link>}
        {onReview && it.review_key && <button className="btn btn-quiet" disabled={busy} onClick={() => onReview(it)}>{busy ? "Saving…" : "Mark reviewed"}</button>}
      </div>
    </article>
  );
}

/** "Changes since your last review" for one plan. Opening it marks nothing; only "Mark reviewed" does, for the exact
 *  filings shown (a filing that arrives meanwhile stays unreviewed). */
export function PlanBriefingView({ planId }: { planId: number }) {
  const [b, setB] = useState<PlanBriefing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(() => { setErr(null); api<PlanBriefing>(`/api/v1/plans/${planId}/briefing`).then(setB).catch((e) => setErr(e.message)); }, [planId]);
  useEffect(load, [load]);
  useEffect(() => {   // returning from Research to "#item-…": bring that item back into view once the briefing has loaded
    if (!b || typeof window === "undefined" || !window.location.hash.startsWith("#item-")) return;
    document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView({ block: "start" });
  }, [b]);
  const review = async (it: BriefingItem) => {
    if (busy) return;
    setBusy(it.id);
    try {
      await api("/api/v1/plans/items/review", { method: "POST", body: JSON.stringify({ review_key: it.review_key, ticker: it.ticker, version_ids: it.version_ids ?? [] }) });
      setNote({ ok: true, text: `${it.ticker} marked reviewed. A later meaningful filing will bring it back.` });
      load();
    } catch (e) { setNote({ ok: false, text: `Not saved: ${(e as Error).message}` }); } finally { setBusy(null); }
  };
  if (err) return <ErrorState message={err} retry={load} />;
  if (!b) return <div className="space-y-3"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-32" /></div>;
  const s = b.sections;
  const changes = s.new.length + s.updated.length;
  const block = (title: string, hint: string, items: BriefingItem[], id: string, reviewable = true) => items.length > 0 && (
    <section className="space-y-2" aria-labelledby={`h-${id}`} data-testid={`plan-${id}`}>
      <div><h3 id={`h-${id}`} className="text-[15px] font-semibold">{title} <span className="num text-ink-3">· {items.length}</span></h3><p className="text-[12.5px] text-ink-3">{hint}</p></div>
      {items.map((it) => <Item key={it.id} it={it} planId={planId} busy={busy === it.id} onReview={reviewable ? review : undefined} />)}
    </section>
  );
  return (
    <div className="space-y-5" data-testid="plan-briefing">
      <div>
        <p className="label">{b.plan.name} · v{b.plan_version}</p>
        <p className="text-[12px] text-ink-3" data-testid="plan-subtitle">{planSubtitle(b.plan)}</p>
        <h2 className="text-[22px] font-semibold leading-tight tracking-tight">{b.paused ? "This plan is paused" : changes === 0 ? "No changes since your last review" : `Changes since your last review · ${changes}`}</h2>
        <p className="mt-1 text-[12.5px] text-ink-3">{b.scope.length} compan{b.scope.length === 1 ? "y" : "ies"} · evaluated {fmtTime(b.evaluated_at)} · {b.schedule}.
          {b.reviewed_unchanged > 0 && ` ${b.reviewed_unchanged} item${b.reviewed_unchanged === 1 ? "" : "s"} you reviewed are unchanged.`}</p>
      </div>
      {note && <p role={note.ok ? "status" : "alert"} className={`rounded-xl border px-3 py-2 text-[13px] ${note.ok ? "border-up/30 bg-up/10" : "border-down/40 bg-down/10"}`}>{note.text}</p>}
      {s.unable.length > 0 && (
        <section className="rounded-xl border border-accent/40 bg-accent/5 p-3" aria-labelledby="h-unable" data-testid="plan-unable">
          <h3 id="h-unable" className="text-[14px] font-semibold">Unable to evaluate · {s.unable.length}</h3>
          <p className="text-[12.5px] text-ink-3">FINN could not check these, so “no change” is not known for them.</p>
          <ul className="mt-1.5 space-y-1 text-[12.5px] text-ink-2">{s.unable.map((u) => <li key={u.id}><span className="font-semibold">{u.ticker}</span> — {u.reason}</li>)}</ul>
        </section>
      )}
      {block("Meaningful updates", "Items you reviewed that have a new filing that changes them — the new filing first.", s.updated, "updated")}
      {block("New", "Matches for this plan that you haven't reviewed.", s.new, "new")}
      {s.resolved.length > 0 && (
        <section className="space-y-1" aria-labelledby="h-res" data-testid="plan-resolved"><h3 id="h-res" className="text-[15px] font-semibold">Resolved questions · {s.resolved.length}</h3>
          <ul className="space-y-1.5 text-[12.5px]">{s.resolved.map((r) => <li key={r.id} className="card p-2.5"><span className="font-semibold">{r.ticker}</span> · {r.question} — answered by <Step inline d={r.resolved_by!} /></li>)}</ul></section>
      )}
      {s.unresolved.length > 0 && (
        <section className="space-y-1" aria-labelledby="h-open" data-testid="plan-unresolved"><h3 id="h-open" className="text-[15px] font-semibold">Still open · {s.unresolved.length}</h3>
          <ul className="space-y-1 text-[12.5px] text-ink-2">{s.unresolved.map((r) => <li key={r.id}><span className="font-semibold">{r.ticker}</span> · {r.question}{r.expected_by ? ` — expected by ${fmtDate(r.expected_by)}` : ""}
                    <span className="block text-[11.5px] text-ink-3">{(r.basis ?? "").length > 140 ? `${r.basis!.slice(0, 138)}…` : r.basis}</span></li>)}</ul></section>
      )}
      {b.repeats_suppressed.length > 0 && (
        <details className="text-[12.5px] text-ink-3" data-testid="plan-repeats"><summary className="cursor-pointer">{b.repeats_suppressed.length} repeated disclosure{b.repeats_suppressed.length === 1 ? "" : "s"} not shown as new</summary>
          <ul className="mt-1 space-y-1">{b.repeats_suppressed.map((r) => <li key={r.id}>{r.ticker}: {r.note} {r.new_filings?.map((d) => <Step inline key={d.id} d={d} />)}</li>)}</ul></details>
      )}
      {changes === 0 && s.unable.length === 0 && !b.paused && <p className="card px-4 py-5 text-center text-[13px] text-ink-3">Nothing new or changed for this plan since your last review.</p>}
    </div>
  );
}

/** Briefing-page block: every active plan's return briefing, or a prompt to create one. */
export function HandoverBriefing({ planId }: { planId?: number | null }) {
  const [plans, setPlans] = useState<MonitoringPlan[] | null>(null);
  const [sel, setSel] = useState<number | null>(planId ?? null);
  useEffect(() => { api<MonitoringPlan[]>("/api/v1/plans").then((p) => { setPlans(p); if (!sel && p.length) setSel(p[0].id); }).catch(() => setPlans([])); }, [sel]);
  if (!plans) return null;
  if (!plans.length) return (
    <div className="rounded-xl border border-line bg-surface-2/50 p-3 text-[13px] text-ink-2">
      Hand FINN a monitoring plan — for example guidance cuts, CFO resignations or fund-raise updates — and it will show what changed since you last reviewed.
      <Link href="/alerts?tab=plans" className="btn btn-secondary mt-2 block w-fit">What should FINN watch?</Link></div>
  );
  return (
    <div className="space-y-3">
      {plans.length > 1 && (
        <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Plan">{plans.map((p) =>
          <button key={p.id} aria-pressed={sel === p.id} onClick={() => setSel(p.id)} data-testid={`plan-chip-${p.id}`}
            className="stat min-h-[56px] py-2">
            <span className="block text-[13px] font-semibold text-ink">{p.name}{p.enabled ? "" : " · paused"}</span><span className="block text-[11.5px] text-ink-3">{planSubtitle(p)}</span></button>)}</div>
      )}
      {sel && <PlanBriefingView planId={sel} />}
    </div>
  );
}
