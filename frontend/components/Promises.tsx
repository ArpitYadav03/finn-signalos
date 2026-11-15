"use client";
import type { PromiseItem, PromiseTracker } from "@/lib/types";
import { CiteChip } from "./EvidenceSheet";
import { EmptyState } from "./ui";

/** Promise Ledger: genuine forward-looking guidance only, split into dated commitments (scored against filed results),
 *  long-term aspirations (context, not scored) and other statements (no number or no comparable measure). */

const STATUS: Record<string, { label: string; cls: string; plain: string }> = {
  met: { label: "Delivered", cls: "bg-up/15 text-up", plain: "The reported figure met the target." },
  missed: { label: "Missed", cls: "bg-down/15 text-down", plain: "The reported figure fell short of the target." },
  tracking_on: { label: "On track so far", cls: "bg-up/10 text-up", plain: "Part-way through the period, results so far are in line with the target." },
  tracking_below: { label: "Behind so far", cls: "bg-down/10 text-down", plain: "Part-way through the period, results so far are below the target." },
  pending: { label: "Not yet reported", cls: "bg-surface-2 text-ink-2", plain: "No results for the target period have been filed yet." },
  long_term: { label: "Long-term aim", cls: "bg-info/15 text-info", plain: "A multi-year ambition; shown for context, not scored." },
  not_comparable: { label: "Not comparable", cls: "bg-surface-2 text-ink-3", plain: "The target can't be checked like-for-like against filed results." },
  unavailable: { label: "Can't verify", cls: "bg-surface-2 text-ink-3", plain: "The figure needed to check this isn't available." },
};

const month = (d: string | null | undefined) =>
  d ? new Date(d.length === 7 ? `${d}-01` : d).toLocaleDateString("en-IN", { month: "short", year: "numeric" }) : "—";

function Gauge({ it }: { it: PromiseItem }) {
  const a = it.comparison.actual_value;
  const lo = it.target_min, hi = it.target_max, tv = it.target_value;
  if (a === null || it.metric === "capex") return null;
  const tMin = lo ?? tv ?? 0, tMax = hi ?? tv ?? tMin;
  const min = Math.min(a, tMin) - 3, max = Math.max(a, tMax) + 3;
  const x = (v: number) => `${((v - min) / (max - min)) * 100}%`;
  return (
    <div className="relative mt-3 h-8" role="img" aria-label={`Reported ${a.toFixed(1)}% against a target of ${it.target_text}`}>
      <div className="absolute inset-x-0 top-3.5 h-1 rounded-full bg-surface-2" />
      <div className="absolute top-2.5 h-3 rounded bg-accent/35" style={{ left: x(tMin), width: `calc(${x(tMax)} - ${x(tMin)} + 3px)` }} />
      <div className="absolute top-1 h-6 w-0.5 bg-ink" style={{ left: x(a) }} />
      <span className="num absolute -bottom-1 text-[11px] text-ink" style={{ left: x(a), transform: "translateX(-50%)" }}>{a.toFixed(1)}%</span>
    </div>
  );
}

function Card({ it }: { it: PromiseItem }) {
  const st = STATUS[it.comparison.status] || STATUS.unavailable;
  const notes = it.explanations ?? [...(it.conditions ? [it.conditions] : []), ...it.comparison.caveats];
  const scored = it.section === "commitment";
  return (
    <article className="card p-4" data-testid="promise-item">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[14px] font-semibold">{it.metric_label}</p>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${st.cls}`}>{st.label}</span>
      </div>
      <p className="mt-0.5 text-[12px] text-ink-3">{st.plain}</p>
      <dl className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <dt className="label">Promised</dt>
          <dd className="num text-[18px]">{it.target_text}</dd>
          <dd className="text-[12px] text-ink-3">for {it.target_period || (it.horizon === "long_term" ? "the long term" : "no stated period")}{it.basis && it.basis !== "unspecified" ? ` · ${it.basis}` : ""}</dd>
          <dd className="text-[12px] text-ink-3">said {month(it.guidance_date)}</dd>
        </div>
        <div>
          <dt className="label">Reported</dt>
          <dd className="num text-[18px]">{it.comparison.actual_value !== null ? `${it.comparison.actual_value.toFixed(1)}%` : "—"}</dd>
          <dd className="text-[12px] text-ink-3">{it.comparison.actual_period ? `for ${it.comparison.actual_period}` : scored ? "not reported yet" : "not checked"}
            {it.comparison.gap_pp !== null ? ` · ${it.comparison.gap_pp >= 0 ? "+" : "−"}${Math.abs(it.comparison.gap_pp).toFixed(1)} pp vs target` : ""}</dd>
        </div>
      </dl>
      <Gauge it={it} />
      {it.company_reported && (
        <div className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-[12px] leading-relaxed text-ink-2" data-testid="company-reported">
          <span className="font-medium text-ink">Management&apos;s own figure: {it.company_reported.value}%</span>
          {it.company_reported.consistent !== null && (
            <span className={it.company_reported.consistent ? "text-up" : "text-down"}>
              {it.company_reported.consistent ? " · matches FINN's calculation" : " · differs from FINN's calculation — definitions may differ"}
            </span>
          )}
          <span className="mt-1 block italic">“{it.company_reported.statement}”</span>
          <span className="mt-1 flex flex-wrap items-center gap-2 not-italic text-ink-3">— {it.company_reported.speaker}
            <CiteChip c={it.company_reported.citation} label={`${it.company_reported.citation.reporting_period} call p.${it.company_reported.citation.page_number}`} /></span>
        </div>
      )}
      <blockquote className="mt-3 border-l-2 border-line pl-3 text-[13px] italic leading-relaxed text-ink-2">“{it.statement}”</blockquote>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
        <span>— {it.speaker || "Management"}</span>
        <CiteChip c={it.guidance_citation} label={`${it.guidance_citation.reporting_period ? `${it.guidance_citation.reporting_period} call ` : "transcript "}p.${it.guidance_citation.page_number}`} />
        {it.actual_citation && <CiteChip c={it.actual_citation} label="reported figures" />}
      </div>
      {(it.revision || (it.reiterated_on && it.reiterated_on.length > 0)) && (
        <div className="mt-3 text-[12px] text-ink-2">
          <p className="label">History</p>
          <ul className="mt-1 space-y-0.5">
            {it.revision && <li>{it.revision.changed ? "Revised" : "Reiterated"} from {it.revision.previous_target} (said {month(it.revision.previous_date)})</li>}
            {Array.from(new Set((it.reiterated_on ?? []).filter(Boolean).map((d) => month(d)))).map((m) => <li key={m}>Repeated on the {m} call</li>)}
          </ul>
        </div>
      )}
      {notes.length > 0 && (
        <ul className="mt-3 space-y-0.5 border-t border-line pt-2 text-[12px] leading-relaxed text-ink-3">
          {notes.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      )}
    </article>
  );
}

export function Promises({ data }: { data: PromiseTracker | null }) {
  if (!data) return null;
  const cov = data.coverage;
  if (!data.items.length) {
    if (cov && cov.transcripts_indexed === 0)
      return <EmptyState title="No earnings-call transcripts to read"
        body={cov.transcripts_listed
          ? `${cov.transcripts_listed} transcript${cov.transcripts_listed === 1 ? " is" : "s are"} listed for this company but none could be read yet, so the ledger is empty because of missing data, not because management made no promises.`
          : "The document index lists no earnings-call transcripts for this company (some companies don't publish them). The ledger can't be built, which is not the same as management having given no guidance."} />;
    return <EmptyState title="No forward-looking guidance found"
      body={`FINN read ${cov ? `${cov.transcripts_indexed} earnings-call transcript${cov.transcripts_indexed === 1 ? "" : "s"}${cov.periods.length ? ` (${cov.periods.join(", ")})` : ""}` : "the indexed earnings-call transcripts"} and found no explicit targets from management. Analyst questions and descriptions of past results are deliberately excluded.`} />;
  }
  const section = (it: PromiseItem) => it.section ?? (it.guidance_type === "quantitative" ? "commitment" : "other");
  const commitments = data.items.filter((i) => section(i) === "commitment");
  const aspirations = data.items.filter((i) => section(i) === "aspiration");
  const other = data.items.filter((i) => section(i) === "other");
  const s = data.summary;
  return (
    <div>
      <h3 className="text-[15px] font-semibold">Promise Ledger</h3>
      <p className="mt-1 text-[12.5px] leading-relaxed text-ink-3">Targets management set on earnings calls, checked against results the company later filed. Only completed periods are scored; descriptions of past performance are not promises and are left out.
        {cov ? ` Read from ${cov.transcripts_indexed} transcript${cov.transcripts_indexed === 1 ? "" : "s"}${cov.periods.length ? ` (${cov.periods.join(", ")})` : ""}.` : ""}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-[12px]">
        <span className="rounded-full border border-line px-2.5 py-1">{commitments.length} dated commitment{commitments.length === 1 ? "" : "s"}</span>
        {s.met > 0 && <span className="rounded-full bg-up/15 px-2.5 py-1 text-up">{s.met} delivered</span>}
        {s.missed > 0 && <span className="rounded-full bg-down/15 px-2.5 py-1 text-down">{s.missed} missed</span>}
        {s.tracking > 0 && <span className="rounded-full bg-surface-2 px-2.5 py-1">{s.tracking} in progress</span>}
        {s.revised > 0 && <span className="rounded-full bg-accent/15 px-2.5 py-1 text-accent">{s.revised} revised</span>}
        {aspirations.length > 0 && <span className="rounded-full border border-line px-2.5 py-1 text-ink-2">{aspirations.length} long-term aim{aspirations.length === 1 ? "" : "s"}</span>}
      </div>

      <section className="mt-5" aria-labelledby="pl-commit">
        <h4 id="pl-commit" className="label">Commitments with a target period</h4>
        {commitments.length ? <div className="mt-2 grid gap-3 lg:grid-cols-2">{commitments.map((it) => <Card key={it.id} it={it} />)}</div>
          : <p className="mt-2 text-[12.5px] text-ink-3">None found — management gave no dated, measurable targets in the indexed calls.</p>}
      </section>

      {aspirations.length > 0 && (
        <section className="mt-6" aria-labelledby="pl-asp">
          <h4 id="pl-asp" className="label">Long-term aspirations · not scored</h4>
          <div className="mt-2 grid gap-3 lg:grid-cols-2">{aspirations.map((it) => <Card key={it.id} it={it} />)}</div>
        </section>
      )}

      {other.length > 0 && (
        <details className="card mt-6 p-4">
          <summary className="cursor-pointer text-[13px] text-ink-2">{other.length} other forward-looking statement{other.length === 1 ? "" : "s"} · not scored</summary>
          <p className="mt-2 text-[12px] text-ink-3">These give direction without a number, or name a measure FINN can&apos;t match to filed results.</p>
          <ul className="mt-3 space-y-3">
            {other.map((it) => (
              <li key={it.id} className="text-[12.5px] leading-relaxed text-ink-2">
                <span className="text-ink-3">{it.metric_label} · {month(it.guidance_date)}{it.target_text !== "qualitative" ? ` · ${it.target_text}` : ""} · </span>“{it.statement}” <CiteChip c={it.guidance_citation} />
                {(it.explanations ?? []).slice(0, 1).map((e, i) => <span key={i} className="block text-[12px] text-ink-3">{e}</span>)}
              </li>
            ))}
          </ul>
        </details>
      )}
      <details className="mt-4 text-[12px] text-ink-3">
        <summary className="cursor-pointer">Analysis details</summary>
        <p className="mt-1.5 leading-relaxed">Extractor {data.items[0]?.extractor || "rules-v1"}: management speakers only, forward-looking sentences with a target verb, plausibility limits on numbers. Comparisons are computed by FINN&apos;s finance module and refused when the period or basis (segment, constant currency) doesn&apos;t line up.</p>
      </details>
    </div>
  );
}
