"use client";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { coverageKey, currentCoverage, initialReplayForm, replayFormReducer, validRange, type Mode } from "@/lib/replayForm";
import { api } from "@/lib/api";
import type { MonitoringPlan } from "@/lib/types";
import { fmtDate, fmtDay, fmtPct, fmtPrice, fmtTime } from "@/lib/format";

/* Historical Alert Replay: Select plan → Select dates → Check coverage → Run replay → Inspect matches and limitations.
   Separate from live monitoring: a replay never sends alerts or changes the plan. */

interface Coverage { plan_version: number; session_count: number; non_trading_days: number; limits: string[]; sessions: string[];
  companies: { ticker: string; bars_in_range: number; missing_sessions: string[]; volume_ready_from: string | null; filings_complete_from: string | null }[] }
interface Filing { id: number; role: string; added: string; published_at: string }
interface Match { ticker: string; kind: string; matched: string[]; values?: Record<string, number>; close?: number; previous_close?: number | null;
  filings?: Filing[]; headline?: string; indexed_later?: number[]; event_key?: string;
  guidance_cut?: { metric: string; period: string; basis: string; previous: string; current: string; date: string }; reason?: string }
interface SessionResult { session: string; matches: Match[]; suppressed: Match[]; unable: { ticker: string; kind: string; reason: string }[] }
interface Run { id: number; plan_version: number; spec: { conditions: { kind: string }[] }; conditions: string[]; logical_operator: string; start: string; end: string; mode: string; status: string;
  progress: { done?: number; total?: number; last_session?: string }; error: string | null; created_at: string; finished_at: string | null;
  summary: { sessions_evaluated: number; matches: number; sessions_with_matches: number; suppressed: number; unable: number; sessions_without_matches: number };
  coverage?: Coverage; results?: SessionResult[]; duplicate_of?: number }

const STATUS: Record<string, string> = { queued: "Queued", running: "Running", succeeded: "Finished", cancelled: "Cancelled", interrupted: "Interrupted — can resume", failed: "Failed" };

function MatchLine({ m }: { m: Match }) {
  return (
    <li className="text-[12.5px]">
      <span className="font-semibold">{m.ticker}</span> <span className="text-ink-3">· {m.matched.join(m.values && Object.keys(m.values).length > 1 ? " and " : "; ")}</span>
      {m.values && <span className="num block text-ink-2">{m.values.price_move !== undefined && `${fmtPct(m.values.price_move)} close-to-close`}{m.values.price_move !== undefined && m.values.volume !== undefined && " · "}{m.values.volume !== undefined && `${m.values.volume.toFixed(2)}× prior-20 volume`}{m.close !== undefined && ` · close ${fmtPrice(m.close)}`}</span>}
      {m.filings && <span className="block text-ink-2">{m.headline} — {m.filings.map((f) => `${f.role} (${fmtTime(f.published_at)})`).join(", ")}{m.kind === "update" && " · update to an earlier match"}
        {m.indexed_later && m.indexed_later.length > 0 && <span className="text-accent"> · public then, indexed by FINN later</span>}</span>}
      {m.guidance_cut && <span className="block text-ink-2">{m.guidance_cut.metric.replace(/_/g, " ")} {m.guidance_cut.period}: {m.guidance_cut.previous} → {m.guidance_cut.current} (stated {fmtDate(m.guidance_cut.date)})</span>}
    </li>
  );
}

/** Known gap (backend repair spec item 3): the "FINN had indexed it" cutoff is applied to filings only. Stated, not hidden. */
function IndexedGuidanceGap({ spec }: { spec: { conditions: { kind: string }[] } }) {
  if (!spec.conditions.some((c) => c.kind === "guidance_cut")) return null;
  return <li className="text-accent" data-testid="indexed-guidance-gap">• Guidance cuts are matched by the date management stated them, not by when FINN indexed the transcript, so in this mode a cut may appear before FINN actually had it.</li>;
}

export function ReplayPanel({ plan, onClose }: { plan: MonitoringPlan; onClose: () => void }) {
  const [form, dispatch] = useReducer(replayFormReducer<Coverage & { checked: { start: string; end: string; mode: Mode } }>, undefined,
    () => initialReplayForm<Coverage & { checked: { start: string; end: string; mode: Mode } }>());
  const { start, end, mode, maxDate } = form;
  const cov = currentCoverage(form, plan.id, plan.version);          // null as soon as dates, mode or plan version change
  const stale = !!form.coverage && !cov;
  const rangeError = validRange(form);
  const inFlight = useRef<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const headRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headRef.current?.focus();
    api<{ freshness: { latest_trading_date: string | null } }>("/api/v1/status").then((s) => {
      const last = s.freshness.latest_trading_date;
      if (last) dispatch({ type: "defaults", latest: last });   // fills only fields the user hasn't edited
    }).catch(() => undefined);
    api<Run[]>(`/api/v1/plans/${plan.id}/replays`).then(setRuns).catch(() => undefined);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [plan.id]);

  const poll = useCallback((id: number) => {
    api<Run>(`/api/v1/replays/${id}`).then((r) => {
      setRun(r);
      if (r.status === "queued" || r.status === "running") timer.current = setTimeout(() => poll(id), 1200);
      else api<Run[]>(`/api/v1/plans/${plan.id}/replays`).then(setRuns).catch(() => undefined);
    }).catch((e) => { setErr(`Lost contact with the replay: ${(e as Error).message}. It keeps running on the server — retry to reconnect.`); });
  }, [plan.id]);

  const check = async () => {
    if (busy || rangeError) return;
    const checked = { start, end, mode };
    const key = coverageKey(plan.id, plan.version, checked);
    inFlight.current = key;
    setBusy("check"); setErr(null); setRun(null); dispatch({ type: "clearCoverage" });
    try {
      const c = await api<Coverage>(`/api/v1/plans/${plan.id}/replay/coverage?start=${checked.start}&end=${checked.end}&version=${plan.version}`);
      if (inFlight.current === key) dispatch({ type: "coverage", key, data: { ...c, checked } });   // a reply for older inputs is dropped
    } catch (e) { if (inFlight.current === key) setErr((e as Error).message); }
    finally { if (inFlight.current === key) { inFlight.current = null; setBusy(null); } }
  };
  const go = async () => {
    if (busy || !cov) return;
    setBusy("run"); setErr(null);
    try {
      // Run exactly what was checked and shown, not whatever the inputs say now.
      const { start: s0, end: e0, mode: m0 } = cov.checked;
      const r = await api<Run>(`/api/v1/plans/${plan.id}/replays`, { method: "POST", body: JSON.stringify({ start: s0, end: e0, mode: m0, version: cov.plan_version }) });
      poll(r.duplicate_of ?? r.id);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const cancel = async () => { if (run) { await api(`/api/v1/replays/${run.id}/cancel`, { method: "POST" }).catch(() => undefined); poll(run.id); } };
  const resume = async (id: number) => {
    setErr(null);
    try { await api(`/api/v1/replays/${id}/resume`, { method: "POST" }); poll(id); } catch (e) { setErr((e as Error).message); }
  };

  const active = run && (run.status === "queued" || run.status === "running");
  const results = run?.results ?? [];
  const hits = results.filter((r) => r.matches.length);
  const unableBy = new Map<string, number>();
  results.forEach((r) => r.unable.forEach((u) => { const k = u.reason.replace(/\d{4}-\d{2}-\d{2}/g, "…").replace(/\(\d+ of 20/, "(n of 20"); unableBy.set(k, (unableBy.get(k) ?? 0) + 1); }));

  return (
    <section className="card space-y-3 p-4" aria-labelledby="replay-h" data-testid="replay">
      <div className="flex items-start justify-between gap-2">
        <div><h2 id="replay-h" ref={headRef} tabIndex={-1} className="text-[17px] font-semibold outline-none">Test this plan on past data</h2>
          <p className="text-[12.5px] text-ink-3">“{plan.name}” · rule version v{plan.version}. Separate from live monitoring: no alerts are sent and the plan is not changed.</p></div>
        <button className="btn btn-quiet shrink-0" onClick={onClose} aria-label="Close replay">Close</button>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <label className="text-[12.5px] text-ink-3">From<input type="date" value={start} max={end || maxDate} onChange={(e) => { dispatch({ type: "edit", field: "start", value: e.target.value }); setRun(null); }}
          className="num mt-1 block w-full rounded-lg border border-line bg-bg px-2 py-2 text-[13.5px] text-ink" /></label>
        <label className="text-[12.5px] text-ink-3">To<input type="date" value={end} min={start} max={maxDate} onChange={(e) => { dispatch({ type: "edit", field: "end", value: e.target.value }); setRun(null); }}
          className="num mt-1 block w-full rounded-lg border border-line bg-bg px-2 py-2 text-[13.5px] text-ink" /></label>
        <button className="btn btn-secondary" onClick={check} disabled={!!rangeError || busy !== null || !!active} aria-busy={busy === "check"}>{busy === "check" ? "Checking…" : "Check coverage"}</button>
      </div>
      <fieldset className="text-[12.5px]"><legend className="text-ink-3">Count a filing as available when</legend>
        <div className="mt-1 flex flex-wrap gap-3">
          <label className="flex min-h-[32px] items-center gap-1.5"><input type="radio" name="mode" checked={mode === "public"} onChange={() => { dispatch({ type: "mode", mode: "public" }); setRun(null); }} /> it was published</label>
          <label className="flex min-h-[32px] items-center gap-1.5"><input type="radio" name="mode" checked={mode === "indexed"} onChange={() => { dispatch({ type: "mode", mode: "indexed" }); setRun(null); }} /> FINN had indexed it</label>
        </div></fieldset>
      {rangeError && (start || end) && <p className="text-[12px] text-down">{rangeError}</p>}
      {stale && !run && <p role="status" className="text-[12px] text-ink-3" data-testid="replay-stale">Inputs changed since the last check — check coverage again before running.</p>}
      {err && <p role="alert" className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-[13px]">{err}{run && <button className="ml-2 text-info" onClick={() => { setErr(null); poll(run.id); }}>Retry</button>}</p>}
      {cov && !run && (
        <div className="space-y-2 rounded-xl border border-line bg-surface-2/50 p-3 text-[12.5px]" data-testid="replay-coverage">
          <p className="text-[12.5px] text-ink-2" data-testid="replay-checked">Checked: <span className="num font-medium text-ink">{cov.checked.start}</span> to <span className="num font-medium text-ink">{cov.checked.end}</span> · rule v{cov.plan_version} · filings counted {cov.checked.mode === "indexed" ? "when FINN had indexed them" : "when published"}</p>
          <p className="text-[13.5px] font-medium">{cov.session_count} trading session{cov.session_count === 1 ? "" : "s"} · {cov.non_trading_days} non-trading day{cov.non_trading_days === 1 ? "" : "s"} skipped</p>
          <ul className="space-y-1 text-ink-2">{cov.limits.map((l) => <li key={l}>• {l}</li>)}
            {cov.checked.mode === "indexed" && <IndexedGuidanceGap spec={plan.spec} />}</ul>
          {cov.companies.some((c) => c.missing_sessions.length) && <p className="text-ink-3">Missing bars: {cov.companies.filter((c) => c.missing_sessions.length).map((c) => `${c.ticker} (${c.missing_sessions.length})`).join(", ")}</p>}
          <button className="btn btn-primary w-full sm:w-auto" onClick={go} disabled={busy !== null || cov.session_count === 0}>{busy === "run" ? "Starting…" : `Run replay over ${cov.session_count} sessions`}</button>
        </div>
      )}
      {run && (
        <div className="space-y-3" data-testid="replay-run">
          <div aria-live="polite">
            <p className="text-[13px]"><span className="font-medium">{STATUS[run.status] ?? run.status}</span> · {fmtDay(run.start)} – {fmtDay(run.end)} · v{run.plan_version} · {run.mode === "indexed" ? "as FINN had indexed it" : "as published"}</p>
            {(active || run.status === "interrupted" || run.status === "cancelled") && run.progress.total ? (
              <div className="mt-1.5"><div className="h-1.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuemin={0} aria-valuemax={run.progress.total} aria-valuenow={run.progress.done ?? 0}
                aria-label="Replay progress"><div className="h-full bg-accent transition-[width] motion-reduce:transition-none" style={{ width: `${((run.progress.done ?? 0) / run.progress.total) * 100}%` }} /></div>
                <p className="mt-1 text-[11.5px] text-ink-3">{run.progress.done ?? 0} of {run.progress.total} sessions{run.progress.last_session ? ` · through ${fmtDay(run.progress.last_session)}` : ""}</p></div>) : null}
            <div className="mt-1.5 flex gap-2">
              {active && <button className="btn btn-quiet" onClick={cancel}>Cancel</button>}
              {(run.status === "interrupted" || run.status === "cancelled" || run.status === "failed") && <button className="btn btn-secondary" onClick={() => resume(run.id)}>Resume from {run.progress.last_session ? fmtDay(run.progress.last_session) : "the start"}</button>}
            </div>
            {run.error && <p className="text-[12px] text-down">{run.error}</p>}
          </div>
          {results.length > 0 && (
            <>
              <dl className="grid grid-cols-3 gap-2 text-center text-[12px]" aria-label="Replay summary">
                <div className="rounded-lg border border-line p-2"><dt className="text-ink-3">Sessions with matches</dt><dd className="num text-[18px] font-semibold">{run.summary.sessions_with_matches}</dd></div>
                <div className="rounded-lg border border-line p-2"><dt className="text-ink-3">Repeats suppressed</dt><dd className="num text-[18px] font-semibold">{run.summary.suppressed}</dd></div>
                <div className="rounded-lg border border-line p-2"><dt className="text-ink-3">Unable to evaluate</dt><dd className="num text-[18px] font-semibold">{run.summary.unable}</dd></div>
              </dl>
              <p className="text-[12px] text-ink-3">Rule replayed: {run.conditions.join(run.logical_operator === "AND" ? " AND " : " OR ")}. Matches show what the rule would have flagged; FINN makes no claim about returns or accuracy.</p>
              <section aria-labelledby="rp-m"><h3 id="rp-m" className="text-[14px] font-semibold">Matched sessions · {hits.length}</h3>
                {hits.length === 0 ? <p className="text-[12.5px] text-ink-3">The rule matched nothing in the evaluated sessions.</p> :
                  <ul className="mt-1 space-y-2">{(showAll ? hits : hits.slice(0, 8)).map((r) => (
                    <li key={r.session} className="rounded-lg border border-line p-2.5"><p className="num text-[12px] text-ink-3">{fmtDay(r.session)} {r.session.slice(0, 4)}</p>
                      <ul className="mt-0.5 space-y-1">{r.matches.map((m, i) => <MatchLine key={i} m={m} />)}</ul></li>))}</ul>}
                {!showAll && hits.length > 8 && <button className="btn btn-quiet mt-1" onClick={() => setShowAll(true)}>Show all {hits.length}</button>}
              </section>
              {run.summary.suppressed > 0 && (
                <details className="text-[12.5px]"><summary className="cursor-pointer text-ink-2">Duplicates suppressed · {run.summary.suppressed}</summary>
                  <ul className="mt-1 space-y-1">{results.flatMap((r) => r.suppressed.map((m, i) => <li key={`${r.session}${i}`}><span className="num text-ink-3">{fmtDay(r.session)}</span> <MatchLine m={m} /></li>))}</ul></details>
              )}
              {unableBy.size > 0 && (
                <details className="text-[12.5px]"><summary className="cursor-pointer text-ink-2">Unable to evaluate · {run.summary.unable} checks</summary>
                  <ul className="mt-1 space-y-1 text-ink-3">{[...unableBy.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => <li key={k}>{n}× {k}</li>)}</ul></details>
              )}
              {run.coverage && <section aria-labelledby="rp-lim" className="rounded-lg border border-line p-2.5 text-[12.5px]" data-testid="replay-limits">
                <h3 id="rp-lim" className="text-[13.5px] font-semibold">Limitations of this replay</h3>
                <ul className="mt-1 space-y-1 text-ink-2">{run.coverage.limits.map((l) => <li key={l}>• {l}</li>)}
                  {run.mode === "indexed" && <IndexedGuidanceGap spec={run.spec} />}</ul></section>}
            </>
          )}
        </div>
      )}
      {runs.length > 0 && (
        <details className="text-[12.5px]"><summary className="cursor-pointer text-ink-2">Earlier replays of this plan · {runs.length}</summary>
          <ul className="mt-1 space-y-1">{runs.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2"><button className="text-info" onClick={() => poll(r.id)}>{fmtDay(r.start)} – {fmtDay(r.end)} · v{r.plan_version}</button>
              <span className="text-ink-3">{STATUS[r.status] ?? r.status} · {r.summary.sessions_with_matches} sessions matched</span>
              {r.status === "interrupted" && <button className="text-info" onClick={() => resume(r.id)}>Resume</button>}</li>))}</ul></details>
      )}
    </section>
  );
}
