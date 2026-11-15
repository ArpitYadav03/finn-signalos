"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { researchHref } from "@/lib/researchLink";
import type { Signal } from "@/lib/types";
import { fmtCr, fmtDay, fmtPct, fmtTime, investigateQuestion, tone } from "@/lib/format";
import { CiteChip, useEvidence } from "./EvidenceSheet";
import { LevelBadge } from "./ui";

const ROLE_STYLE: Record<string, string> = {
  "Regulatory approval": "bg-up/15 text-up", "Shareholder approval": "bg-up/15 text-up", "Board decision": "bg-info/15 text-info",
  "Completed": "bg-up/15 text-up", "Revised terms": "bg-accent/15 text-accent", "Advance notice": "bg-surface-2 text-ink-2",
  "Repeat disclosure": "bg-surface-2 text-ink-3", "Repeat (newspaper copy)": "bg-surface-2 text-ink-3",
};

function Figures({ s }: { s: Signal }) {
  if (!s.key_metrics?.length) {
    const size = (s.facts as Record<string, unknown>).size_context;
    return size ? <p className="mt-3 text-[12.5px] text-ink-2"><span className="label mr-2">Size</span><span className="num">{String(size)}</span></p> : null;
  }
  const q = String((s.facts as Record<string, unknown>).results_quarter || "");
  return (
    <div className="no-scrollbar mt-3 overflow-x-auto rounded-lg border border-line">
      <table className="w-full min-w-[300px] text-[12.5px]">
        <caption className="sr-only">{`${s.company_name} headline results for ${q}, ₹ crore, with change versus previous quarter and same quarter last year`}</caption>
        <thead className="bg-surface-2 text-ink-3">
          <tr><th scope="col" className="px-2.5 py-1.5 text-left text-[11px] font-medium uppercase tracking-wider">{q} · ₹ cr</th>
            <th scope="col" className="px-2 text-right text-[11px] font-medium uppercase tracking-wider">Value</th>
            <th scope="col" className="px-2 text-right text-[11px] font-medium uppercase tracking-wider">QoQ</th>
            <th scope="col" className="px-2.5 text-right text-[11px] font-medium uppercase tracking-wider">YoY</th></tr>
        </thead>
        <tbody>
          {s.key_metrics.map((m) => (
            <tr key={m.metric} className="border-t border-line">
              <th scope="row" className="px-2.5 py-1.5 text-left font-normal text-ink-2">{m.label}</th>
              <td className="num whitespace-nowrap px-2 text-right">{fmtCr(m.value)}</td>
              <td className={`num px-2 text-right ${tone(m.qoq.value)}`} title={m.qoq.note || ""}>{m.qoq.value === null ? "n/m" : fmtPct(m.qoq.value)}</td>
              <td className={`num px-2.5 text-right ${tone(m.yoy.value)}`} title={m.yoy.note || ""}>{m.yoy.value === null ? "n/m" : fmtPct(m.yoy.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Array.isArray((s.facts as Record<string, unknown>).results_notes) && (
        <p className="border-t border-line px-2.5 py-1.5 text-[11px] leading-snug text-ink-3">
          {((s.facts as Record<string, unknown>).results_notes as string[]).join(" ")} “n/m” = not meaningful (e.g. a loss in the base period).
        </p>
      )}
    </div>
  );
}

/** Event DNA: what each supporting filing contributed — a new approval, a board decision, or the same disclosure again. */
function EventDNA({ s }: { s: Signal }) {
  const open = useEvidence();
  const items = s.supporting || [];
  if (items.length < 2) return null;
  return (
    <div className="mt-3" data-testid="event-sources">
      <p className="label">What each filing added</p>
      <ol className="mt-1.5 space-y-2 border-l border-line pl-3">
        {items.map((f) => (
          <li key={f.id} className="relative text-[12.5px] leading-snug">
            <span className="absolute -left-[17px] top-1.5 h-2 w-2 rounded-full bg-ink-3" aria-hidden />
            <span className="num mr-2 text-ink-3">{new Date(f.published_at).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" })}</span>
            <span className={`mr-2 rounded px-1.5 py-0.5 text-[11px] font-medium ${ROLE_STYLE[f.role] || "bg-surface-2 text-ink-2"}`}>{f.role}</span>
            <span className="text-ink-2">{f.added}</span>{" "}
            {f.citation ? <button className="text-[12px] text-info underline-offset-2 hover:underline" onClick={() => open(f.citation!)}>source</button>
              : <a className="text-[12px] text-info" href={f.source_url} target="_blank" rel="noreferrer">source ↗</a>}
          </li>
        ))}
      </ol>
    </div>
  );
}

interface FinEvent { instrument: string | null; instrument_family: string | null; action: string; action_label: string; capital_direction: string;
  stage: string; stage_label: string; amount: number | null; unit: string; currency: string; unit_price: number | null; isins: string[];
  dates: string[]; subtype: string; evidence: Record<string, string> }

/** The transaction as structured facts, each with the filing words that support it. */
function Transaction({ fe }: { fe: FinEvent }) {
  const dir = fe.capital_direction === "outflow" ? "Money out (company pays)" : fe.capital_direction === "inflow" ? "Money in (company receives)" : "Not stated";
  const rows: [string, string, string | undefined][] = [
    ["Type", fe.subtype, undefined],
    ["Action", fe.action_label, fe.evidence.action],
    ["Capital direction", dir, undefined],
    ["Stage", fe.stage_label, fe.evidence.stage],
    ["Amount", fe.amount !== null ? `₹${fe.amount.toLocaleString("en-IN", { maximumFractionDigits: 2 })} crore` : "Not stated in the filing", fe.evidence.amount],
  ];
  if (fe.unit_price !== null) rows.push(["Price per unit", `₹${fe.unit_price.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`, fe.evidence.unit_price]);
  if (fe.isins.length) rows.push(["ISIN", fe.isins.join(", "), undefined]);
  if (fe.dates.length) rows.push(["Dates in filing", fe.dates.join(" · "), undefined]);
  return (
    <div>
      <p className="label">Transaction</p>
      <dl className="mt-1 space-y-1.5">
        {rows.map(([k, v, ev]) => (
          <div key={k} className="flex gap-2"><dt className="w-28 shrink-0 text-ink-3">{k}</dt>
            <dd className="min-w-0 text-ink-2">{v}{ev && <span className="mt-0.5 block text-[11.5px] italic text-ink-3">“…{ev}…”</span>}</dd></div>
        ))}
      </dl>
    </div>
  );
}

function Details({ s }: { s: Signal }) {
  const f = s.facts as Record<string, unknown>;
  const fe = f.financial_event as FinEvent | undefined;
  const rows: [string, string][] = [];
  if (Array.isArray(f.people) && f.people.length) rows.push(["Person", String((f.people as string[])[0])]);
  if (f.role) rows.push(["Role", String(f.role)]);
  if (f.action) rows.push(["Change", String(f.action)]);
  if (f.term || f.effective) rows.push(["Term", [f.term, f.effective ? `from ${f.effective}` : null].filter(Boolean).join(" ")]);
  const approvals = [f.regulator_approval ? `${f.regulator_approval} approved` : null, f.shareholder_approval_pending ? "shareholder vote pending" : null].filter(Boolean);
  if (approvals.length) rows.push(["Approvals", approvals.join(" · ")]);
  if (f.tenure_years) rows.push(["Tenure", `~${f.tenure_years} years`]);
  if (f.agency) rows.push(["Agency", String(f.agency)]);
  if (Array.isArray(f.ratings)) rows.push(["Rating", (f.ratings as string[]).join(", ")]);
  if (Array.isArray(f.amounts) && !fe) rows.push(["Amounts in filing", (f.amounts as string[]).join(", ")]);
  return (
    <div className="mt-3 space-y-3 rounded-xl bg-surface-2/70 p-3 text-[12.5px]">
      {rows.length > 0 && (
        <dl className="space-y-1">
          {rows.map(([k, v]) => <div key={k} className="flex gap-2"><dt className="w-28 shrink-0 text-ink-3">{k}</dt><dd className="min-w-0 text-ink-2">{v}</dd></div>)}
        </dl>
      )}
      {fe && <Transaction fe={fe} />}
      {typeof f.profile === "string" && <p className="leading-relaxed text-ink-2"><span className="font-medium text-ink">Profile from the filing: </span>{f.profile}</p>}
      <div>
        <p className="label">Why this priority</p>
        {s.reasons.map((r, i) => (
          <div key={i} className="flex justify-between gap-3 py-0.5"><span className="text-ink-2">{r.label}</span>
            <span className={`num ${r.points >= 0 ? "text-ink" : "text-down"}`}>{r.points > 0 ? "+" : r.points < 0 ? "−" : ""}{Math.abs(r.points)}</span></div>
        ))}
        <div className="mt-1 flex justify-between border-t border-line pt-1 font-semibold"><span>Priority score</span><span className="num">{s.importance_score}</span></div>
      </div>
      <p className="text-[11.5px] text-ink-3">Analysis details: classified by {s.analysis_method}{s.requires_review ? " (low confidence — check the filing)" : ""}; processed {fmtTime(s.processed_at)}.{s.is_sample ? " SAMPLE DATA." : ""}</p>
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = { met: "Delivered", missed: "Missed", tracking_on: "On track so far", tracking_below: "Behind so far",
  long_term: "Long-term aim (context, not scored)", pending: "Not yet reported" };
const RELATION_LABEL: Record<string, string> = { stated: "Stated on this call", reiterated: "Reiterated on this call",
  revised: "Revised on this call", result: "Result for the guided period" };

/** Event DNA ↔ Promise Ledger: guidance connected to this event by its own documents, with the deterministic comparison. */
function GuidanceConnection({ s }: { s: Signal }) {
  const links = s.promise_links || [];
  if (!links.length) return null;
  return (
    <div data-testid="guidance-connection">
      <p className="label">Guidance connection</p>
      <ul className="mt-1.5 space-y-3">
        {links.map((l) => (
          <li key={l.id} className="rounded-lg border border-line p-3">
            <p className="text-[12px] text-ink-3">{RELATION_LABEL[l.relation]} · {l.metric_label}</p>
            <dl className="mt-1 grid grid-cols-2 gap-2 text-[12.5px]">
              <div><dt className="text-ink-3">Guidance</dt><dd className="num text-ink">{l.target_text} <span className="text-ink-3">· {l.target_period || (l.horizon === "long_term" ? "no dated period" : "—")}</span></dd></div>
              <div><dt className="text-ink-3">Reported (calculated)</dt><dd className="num text-ink">{l.actual_value !== null ? `${l.actual_value.toFixed(1)}%` : "—"} <span className="text-ink-3">· {l.actual_period}</span></dd></div>
            </dl>
            <p className="mt-1 text-[12px] text-ink-2"><span className="font-medium">{STATUS_LABEL[l.status] || l.status}.</span>
              {l.status !== "long_term" && l.method ? ` ${l.method}.` : ""}{l.basis !== "reported" ? ` Basis: ${l.basis}.` : " Company-level, reported basis."}</p>
            {l.revision && <p className="text-[12px] text-ink-3">{l.revision.changed ? "Changed" : "Unchanged"} from {l.revision.previous_target}
              {l.revision.previous_date ? ` (said ${new Date(`${l.revision.previous_date}T12:00:00`).toLocaleDateString("en-IN", { month: "short", year: "numeric" })})` : ""}</p>}
            <blockquote className="mt-2 border-l-2 border-line pl-2 text-[12px] italic text-ink-2">“{l.statement}”{l.speaker ? ` — ${l.speaker}` : ""}</blockquote>
            <div className="mt-1.5 flex flex-wrap gap-2">
              <CiteChip c={l.guidance_citation} label={`transcript p.${l.guidance_citation.page_number}`} />
              {l.actual_citation && <CiteChip c={l.actual_citation} label="reported figures" />}
            </div>
            {l.explanations.length > 0 && <p className="mt-1 text-[11.5px] text-ink-3">{l.explanations.join(" ")}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function NextEvidence({ s, onReview }: { s: Signal; onReview?: (action: string, milestone?: string) => void }) {
  const ms = s.milestones || [];
  const watching = s.review?.status === "watching" ? s.review.watch : null;
  if (!ms.length && !watching) return null;
  return (
    <div data-testid="next-evidence">
      <p className="label">Next evidence to watch</p>
      <ul className="mt-1.5 space-y-2">
        {ms.map((m) => {
          const on = watching?.kind === m.kind;
          return (
            <li key={m.kind} className="text-[12.5px]">
              <span className="text-ink">{m.label}</span>{m.expected_by && <span className="num text-ink-3"> · due by {new Date(`${m.expected_by}T12:00:00+05:30`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" })}</span>}
              <span className="block text-[11.5px] text-ink-3">{m.basis}</span>
              {onReview && (
                <span className="mt-1 flex flex-wrap items-center gap-2">
                  {on ? <span className="rounded bg-info/15 px-1.5 py-0.5 text-[11px] text-info">Watching · suggested monitoring, not an alert</span>
                    : <button className="btn btn-quiet min-h-[32px] px-2 text-[12px]" onClick={() => onReview("watch", m.kind)}>Watch for this</button>}
                  {on && m.alert_categories.length > 0 && <Link href={`/alerts?prefill=${encodeURIComponent(`Alert me on any ${m.alert_categories[0].replace("_", " ")} filing from ${s.ticker}`)}`} className="text-[12px] text-info">Turn into an alert →</Link>}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function SignalCard({ s, onReview }: { s: Signal; onReview?: (action: string, milestone?: string) => void }) {
  const [open, setOpen] = useState(false);
  const openEvidence = useEvidence();
  const q = investigateQuestion(s.category, s.headline);
  // Return to exactly where the card was shown (Digest, Unreviewed, a window), so Research can offer "Back to briefing".
  const [from, setFrom] = useState<string | null>(null);
  useEffect(() => { setFrom(window.location.pathname + window.location.search); }, []);
  const href = researchHref({ ticker: s.ticker, filingId: s.id, question: q, from });
  const filings = Math.max(1, s.supporting?.length || 1 + s.related.length);
  const primary = s.citations.find((c) => c.kind === "filing") || s.citations[0];
  const newFilings = s.review?.new_filings || [];
  const changed = s.what_changed && s.what_changed !== s.headline ? s.what_changed : s.summary !== s.headline ? s.summary : null;
  const fe = (s.facts as Record<string, unknown>).financial_event as FinEvent | undefined;
  const extras = (s.supporting?.length || 0) > 1 || (s.promise_links?.length || 0) > 0 || (s.milestones?.length || 0) > 0 || !!s.key_metrics?.length;
  return (
    <article className="card p-4 md:p-5" data-testid="signal-card" aria-labelledby={`sig-${s.id}`}>
      <div className="flex items-center gap-2.5 text-[12px] text-ink-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface-3 text-[12px] font-bold text-ink" aria-hidden>{s.ticker.slice(0, 2)}</span>
        <span className="min-w-0 leading-tight"><span className="block text-[13.5px] font-semibold text-ink">{s.ticker}</span>
          <span className="block truncate">{s.category_label}{fe ? ` · ${fe.subtype.split(" — ")[0]}` : ""}</span></span>
        {s.review?.update && <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent">{s.review.update}</span>}
        <span className="ml-auto shrink-0"><LevelBadge level={s.importance_level} /></span>
      </div>
      <p className="mt-3 sr-only">What happened</p>
      <h3 id={`sig-${s.id}`} className="mt-3 text-[17px] font-semibold leading-snug tracking-tight">{s.headline}</h3>
      {(newFilings.length > 0 || changed) && (
        <div className="mt-2.5">
          <p className="label">{newFilings.length ? "New since your review" : "What is new"}</p>
          {newFilings.length ? (
            <ul className="mt-0.5 space-y-0.5 text-[13.5px] leading-relaxed text-ink-2">
              {newFilings.map((f) => <li key={f.id}><span className="mr-1.5 rounded bg-surface-2 px-1.5 text-[11px] text-ink-2">{f.role}</span>{f.added} <span className="text-[11.5px] text-ink-3">· {fmtTime(f.published_at)}</span></li>)}
            </ul>
          ) : <p className="text-[13.5px] leading-relaxed text-ink-2">{changed}</p>}
        </div>
      )}
      {s.why_it_matters && (
        <div className="mt-3 rounded-xl bg-accent/[0.07] px-3 py-2.5 ring-1 ring-inset ring-accent/15">
          <p className="text-[11px] font-semibold uppercase tracking-[.09em] text-accent">Why it deserves attention</p>
          <p className="mt-0.5 text-[13.5px] leading-relaxed text-ink-2">{s.why_it_matters}</p>
        </div>
      )}
      <p className="mt-3 text-[12px] text-ink-3">
        Filed {fmtTime(s.published_at)} · {filings} filing{filings > 1 ? "s" : ""}
        {s.market && s.market.return_pct !== null && s.market.trading_date && (
          <span title={s.market.caution}> · first full session after filing ({fmtDay(s.market.trading_date)}): <span className={`num ${tone(s.market.return_pct)}`}>{fmtPct(s.market.return_pct)}</span></span>
        )}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
        {primary && <button onClick={() => openEvidence(primary.kind === "structured_results" ? { ...primary, ticker: s.ticker } : primary)} className="btn btn-secondary">View evidence</button>}
        <Link href={href} className="btn btn-primary">Investigate</Link>
        {onReview && s.review?.status !== "reviewed" && (
          <button onClick={() => onReview("reviewed")} className="btn btn-quiet" data-testid="mark-reviewed">Mark reviewed</button>
        )}
      </div>
      {extras && (
        <button onClick={() => setOpen((v) => !v)} className="mt-2 min-h-[36px] text-[12.5px] text-ink-3 hover:text-ink" aria-expanded={open}>
          {open ? "Hide timeline, guidance and scoring" : `Timeline, ${s.promise_links?.length ? "guidance, " : ""}next evidence and scoring`}
        </button>
      )}
      {open && (
        <div className="mt-2 space-y-4 rounded-xl bg-surface-2/50 p-3">
          <Figures s={s} />
          <EventDNA s={s} />
          <GuidanceConnection s={s} />
          <NextEvidence s={s} onReview={onReview} />
          <Details s={s} />
        </div>
      )}
      {!open && s.review?.status === "watching" && s.review.watch && (
        <p className="mt-1 text-[12px] text-info">Watching: {s.review.watch.label} (suggested monitoring, not an alert)</p>
      )}
    </article>
  );
}

export function RoutineRow({ s }: { s: Signal }) {
  return (
    <li className="flex items-start gap-3 py-2.5">
      <span className="num mt-0.5 w-20 shrink-0 text-[12px] font-semibold text-ink-2">{s.ticker}</span>
      <div className="min-w-0 flex-1">
        <a href={s.source_url} target="_blank" rel="noreferrer" className="block truncate text-[13px] text-ink-2 hover:text-ink">{s.headline}</a>
        <p className="text-[11.5px] text-ink-3">{s.category_label}
          {(s.facts as Record<string, unknown>).financial_event ? ` (${((s.facts as Record<string, unknown>).financial_event as FinEvent).subtype.split(" — ")[0].toLowerCase()})` : ""} · {fmtTime(s.published_at)}</p>
      </div>
    </li>
  );
}
