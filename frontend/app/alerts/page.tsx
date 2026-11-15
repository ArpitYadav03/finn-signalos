"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { AlertItem, AlertRule, EditableCondition, RulePreview } from "@/lib/types";
import { ago, fmtDay, fmtTime } from "@/lib/format";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { PlanComposer, PlanList } from "@/components/Handover";
import { ReplayPanel } from "@/components/Replay";
import type { MonitoringPlan } from "@/lib/types";

interface Prefs { price_move_pct: number; volume_multiple: number; watched_categories: string[]; categories: { value: string; label: string }[] }
interface ProposedAction { type: string; description: string; preview?: RulePreview; duplicate_of?: { id: number; sentence: string }; [k: string]: unknown }
interface AssistantReply { reply: string; mode: string; steps: { tool: string; ok: boolean }[]; actions: ProposedAction[]; warnings?: string[] }
interface EvalStatus { last_run: { at: string; rules_evaluated: number; new_alerts: number; eod_session: string | null } | null; latest_eod_session: string }
interface Status {
  freshness: { last_ingestion_run: string | null; last_ingestion_status: string | null; latest_trading_date: string | null; ai_mode: string };
  scheduler?: { enabled: boolean; next_run: string | null; running: boolean; times_ist: string };
  counts: Record<string, number>;
}
type Tab = "rules" | "history" | "thresholds";
type Note = { ok: boolean; text: string };

// Short, scannable starters; tapping one fills the box so it can be reviewed before sending.
const EXAMPLES: { label: string; text: string }[] = [
  { label: "Reliance falls 4%", text: "Notify me if Reliance falls more than 4% in a day" },
  { label: "Unusual volume in TCS", text: "Alert me if TCS volume exceeds twice the average" },
  { label: "CEO or CFO change", text: "Alert me on any CFO or CEO change" },
  { label: "Rating downgrades", text: "Alert me on any credit rating downgrade" },
  { label: "Pause an alert", text: "Pause the Reliance alert" },
];
const STEPS = ["Describe", "Review", "Save", "History"];

function Steps({ at }: { at: number }) {
  return (
    <ol className="flex items-center gap-1.5 text-[11.5px]" aria-label="Steps">
      {STEPS.map((s, i) => (
        <li key={s} className="flex items-center gap-1.5" aria-current={i === at ? "step" : undefined}>
          <span className={`grid h-5 w-5 place-items-center rounded-full text-[10.5px] font-semibold ${i < at ? "bg-up/20 text-up" : i === at ? "bg-accent text-accent-ink" : "bg-surface-2 text-ink-3"}`}>{i < at ? "✓" : i + 1}</span>
          <span className={i === at ? "font-medium text-ink" : "text-ink-3"}>{s}</span>
          {i < STEPS.length - 1 && <span className="h-px w-3 bg-line" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}

function Toggle({ on, onChange, label }: { on: boolean; onChange: () => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={onChange}
      className="relative grid h-11 w-12 shrink-0 place-items-center">
      <span className={`relative h-6 w-11 rounded-full transition-colors ${on ? "bg-accent" : "bg-line"}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
      </span>
    </button>
  );
}

function Stepper({ value, step, min, max, format, onChange, label }: {
  value: number; step: number; min: number; max: number; format: (v: number) => string; onChange: (v: number) => void; label: string;
}) {
  const set = (v: number) => onChange(Math.min(max, Math.max(min, Math.round(v * 100) / 100)));
  return (
    <div className="flex items-center gap-1" role="group" aria-label={label}>
      <button onClick={() => set(value - step)} disabled={value <= min} className="grid h-10 w-10 place-items-center rounded-lg border border-line text-lg text-ink-2 disabled:opacity-30" aria-label={`Decrease ${label}`}>−</button>
      <span className="num w-16 text-center text-[15px] font-semibold" aria-live="polite">{format(value)}</span>
      <button onClick={() => set(value + step)} disabled={value >= max} className="grid h-10 w-10 place-items-center rounded-lg border border-line text-lg text-ink-2 disabled:opacity-30" aria-label={`Increase ${label}`}>+</button>
    </div>
  );
}

/** Scope, conditions (with AND/OR spelled out), and when it is checked — shown before saving and on every saved rule. */
function PreviewBlock({ p }: { p: RulePreview }) {
  return (
    <dl className="grid gap-2 text-[13px] sm:grid-cols-[110px_1fr]" data-testid="rule-preview">
      <dt className="text-ink-3">Watches</dt><dd className="text-ink">{p.scope}</dd>
      <dt className="text-ink-3">Alerts when</dt>
      <dd className="text-ink">
        <ul className="space-y-0.5">
          {p.conditions.map((c, i) => (
            <li key={i}>{i > 0 && p.combine && <span className="mr-1.5 rounded bg-surface-2 px-1 text-[11px] font-semibold text-ink-2">{p.combine.includes("ANY") ? "OR" : "AND"}</span>}
              {c.charAt(0).toUpperCase() + c.slice(1)}</li>
          ))}
        </ul>
        {p.combine && <p className="mt-1 text-[12px] text-ink-3">{p.combine}.</p>}
      </dd>
      {p.min_importance && <><dt className="text-ink-3">Filings</dt><dd className="text-ink-2">Only filings rated {p.min_importance} priority or higher</dd></>}
      <dt className="text-ink-3">Checked</dt><dd className="text-ink-2">{p.frequency}</dd>
    </dl>
  );
}

function condLabel(c: EditableCondition): { text: string; unit: string; magnitude: number } {
  if (c.metric === "volume_ratio") return { text: "Volume above", unit: "× 20-day average", magnitude: c.value };
  if (c.metric === "abs_daily_return_pct") return { text: "Moves at least ±", unit: "% in a day", magnitude: Math.abs(c.value) };
  if (c.operator === "lt" || c.operator === "lte" || c.value < 0) return { text: "Falls more than", unit: "% in a day", magnitude: Math.abs(c.value) };
  return { text: "Rises more than", unit: "% in a day", magnitude: Math.abs(c.value) };
}

function EvalLine({ r }: { r: AlertRule }) {
  const ev = r.last_evaluation;
  if (!r.enabled) return <span className="text-ink-3">Paused — not checked until resumed</span>;
  if (!ev) return <span className="text-ink-3">Not checked yet — runs at the next refresh</span>;
  const session = ev.eod_session ? `${fmtDay(ev.eod_session)} close` : `filings up to ${fmtTime(ev.at)}`;
  const status = ev.status ?? (ev.matched.length ? "matched" : "no_match");
  const pill = status === "matched" ? "bg-accent/15 text-accent" : status === "unable" ? "bg-down/10 text-down" : "bg-surface-2 text-ink-2";
  return (
    <div className="space-y-1">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={`rounded-full px-2 py-0.5 text-[12px] font-medium ${pill}`}>
          {status === "matched" ? `Matched · ${ev.matched.join(", ")}` : status === "unable" ? "Unable to evaluate" : "Not matched"}
        </span>
        <span className="text-ink-3">{session} · checked {ago(ev.at)}</span>
      </p>
      {status === "unable" && ev.unable && ev.unable.length > 0 && <p className="text-[12px] text-ink-3">{ev.unable.join("; ")}. FINN treats missing data as unknown, not as “no move”.</p>}
    </div>
  );
}

function RuleCard({ r, onToggle, onDelete, onSave, fresh }: {
  r: AlertRule; onToggle: () => void; onDelete: () => void; onSave: (conds: EditableCondition[]) => Promise<boolean>; fresh: boolean;
}) {
  const [confirmDel, setConfirmDel] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);
  const editable = r.conditions_editable || [];
  const startEdit = () => { setDraft(Object.fromEntries(editable.map((c) => [c.index, String(condLabel(c).magnitude)]))); setEditing(true); };
  const valid = editable.every((c) => Number(draft[c.index]) > 0);
  const save = async () => {
    setSaving(true);
    const ok = await onSave(editable.map((c) => {
      const m = Number(draft[c.index]);
      const neg = c.metric === "daily_return_pct" && (c.operator === "lt" || c.operator === "lte" || c.value < 0);
      return { ...c, value: neg ? -m : m };
    }));
    setSaving(false);
    if (ok) setEditing(false);   // on failure the edited values stay in place
  };
  return (
    <li className={`card p-4 ${fresh ? "ring-1 ring-accent/50" : ""}`} data-testid="rule-card">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[14.5px] font-semibold">{r.ticker ? r.subject : "All holdings"}</p>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${r.enabled ? "bg-up/15 text-up" : "bg-surface-2 text-ink-3"}`}>{r.enabled ? "Active" : "Paused"}</span>
          </div>
          <p className="mt-1 text-[13.5px] leading-snug text-ink-2">{r.sentence.charAt(0).toUpperCase() + r.sentence.slice(1)}</p>
        </div>
        <Toggle on={r.enabled} onChange={onToggle} label={`${r.enabled ? "Pause" : "Resume"} the alert for ${r.subject}`} />
      </div>
      {r.preview && (
        <details className="mt-2 text-[12.5px]">
          <summary className="cursor-pointer py-1 text-ink-3">Rule details</summary>
          <div className="mt-2 rounded-lg bg-surface-2/60 p-3"><PreviewBlock p={r.preview} /></div>
        </details>
      )}
      {editing && (
        <form className="mt-3 space-y-2 rounded-lg border border-line p-3" onSubmit={(e) => { e.preventDefault(); if (valid) save(); }}>
          {editable.map((c) => {
            const l = condLabel(c);
            return (
              <label key={c.index} className="flex flex-wrap items-center gap-2 text-[13px] text-ink-2">
                {l.text}
                <input type="number" inputMode="decimal" step="0.5" min="0.5" value={draft[c.index] ?? ""} aria-label={`${l.text} ${l.unit}`}
                  onChange={(e) => setDraft((d) => ({ ...d, [c.index]: e.target.value }))}
                  className="num w-20 rounded-lg border border-line bg-bg px-2 py-1.5 text-[14px] text-ink outline-none focus:border-accent" />
                {l.unit}
              </label>
            );
          })}
          {!valid && <p className="text-[12px] text-down">Each threshold must be a number above zero.</p>}
          <div className="flex gap-2 pt-1">
            <button className="btn btn-primary" disabled={!valid || saving}>{saving ? "Saving…" : "Save and re-check"}</button>
            <button type="button" className="btn btn-quiet" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </form>
      )}
      <div className="mt-3 border-t border-line pt-3 text-[12.5px]"><EvalLine r={r} /></div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {editable.length > 0 && !editing && <button className="btn btn-secondary" onClick={startEdit}>Edit threshold</button>}
        {!confirmDel ? (
          <button className="btn btn-quiet ml-auto text-ink-3" onClick={() => setConfirmDel(true)}>Delete</button>
        ) : (
          <span className="ml-auto flex items-center gap-2 text-[12.5px] text-ink-2" role="group" aria-label="Confirm delete">
            Delete this alert?
            <button onClick={onDelete} className="btn min-h-[36px] bg-down px-3 text-white">Delete</button>
            <button onClick={() => setConfirmDel(false)} className="btn btn-quiet min-h-[36px]">Keep</button>
          </span>
        )}
      </div>
    </li>
  );
}

function AlertCard({ a, rule, onDismiss }: { a: AlertItem; rule?: AlertRule; onDismiss: () => void }) {
  const text = a.message.replace(new RegExp(`^${a.ticker}:\\s*`), "").replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (d) => fmtDay(d));
  const href = `/research?ticker=${a.ticker}${a.related_event_id ? `&filing=${a.related_event_id}` : ""}`;
  return (
    <li className="card p-4" data-testid="alert-card">
      <div className="flex items-baseline gap-2">
        <p className="min-w-0 truncate text-[14px] font-semibold">{a.company_name || a.ticker}</p>
        <span className="shrink-0 whitespace-nowrap text-[12px] text-ink-3">{a.type === "announcement" ? "Filing" : "Price & volume"}</span>
        {a.status === "new" && <span className="ml-auto rounded-full bg-accent px-1.5 text-[11px] font-semibold text-accent-ink">New</span>}
      </div>
      <p className="mt-1 text-[13.5px] leading-snug text-ink-2">{text}</p>
      <p className="mt-1 text-[12px] text-ink-3">
        {a.as_of ? `${a.type === "announcement" ? "Filed" : "Session"} ${fmtDay(a.as_of)} · ` : ""}raised {ago(a.created_at)}
        {rule ? <> · from “{rule.sentence}”</> : a.rule_id ? " · from a deleted alert" : ""}
      </p>
      <div className="mt-3 flex gap-2">
        <Link href={href} className="btn btn-primary">Investigate</Link>
        <button onClick={onDismiss} className="btn btn-quiet">Dismiss</button>
      </div>
    </li>
  );
}

function AlertRulesView() {
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[] | null>(null);
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [evalStatus, setEvalStatus] = useState<EvalStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reply, setReply] = useState<AssistantReply | null>(null);
  const [picked, setPicked] = useState<boolean[]>([]);
  const [note, setNote] = useState<Note | null>(null);
  const [convo, setConvo] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [checking, setChecking] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [freshIds, setFreshIds] = useState<number[]>([]);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const boxRef = useRef<HTMLTextAreaElement | null>(null);

  const flash = (ok: boolean, t: string) => { setNote({ ok, text: t }); setTimeout(() => setNote(null), ok ? 5000 : 9000); };

  const load = useCallback(() => {
    setErr(null);
    return Promise.all([api<AlertRule[]>("/api/v1/alert-rules"), api<AlertItem[]>("/api/v1/alerts"), api<Prefs>("/api/v1/preferences"),
                 api<Status>("/api/v1/status"), api<EvalStatus>("/api/v1/alert-rules/status")])
      .then(([r, a, p, s, e]) => { setRules(r); setAlerts(a); setPrefs(p); setStatus(s); setEvalStatus(e); return r; })
      .catch((e) => { setErr(e.message); return null; });
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {   // "Turn into an alert" from a watched milestone pre-fills the description; nothing is saved without review
    try {
      const p = new URLSearchParams(window.location.search).get("prefill");
      if (p) { setText(p); setTimeout(() => boxRef.current?.focus(), 0); }
    } catch { /* ignore */ }
  }, []);

  const visibleAlerts = useMemo(() => (alerts || []).filter((a) => a.status !== "dismissed"), [alerts]);
  const newCount = visibleAlerts.filter((a) => a.status === "new").length;
  const activeTab: Tab = tab ?? (newCount > 0 ? "history" : "rules");
  const ruleById = useMemo(() => new Map((rules || []).map((r) => [r.id, r])), [rules]);
  const step = reply?.actions.length ? 1 : 0;

  const send = async (m: string) => {
    if (m.trim().length < 2 || busy) return;
    setBusy(true); setReply(null);
    try {
      const r = await api<AssistantReply>("/api/v1/assistant/alerts", { method: "POST", body: JSON.stringify({ message: m, history: convo.slice(-6) }) });
      setReply(r); setPicked(r.actions.map((a) => !a.duplicate_of));   // duplicates of existing alerts start unticked
      setConvo((c) => [...c, { role: "user", content: m }, { role: "assistant", content: r.reply }]);
    } catch (e) { flash(false, `Couldn't read that request: ${(e as Error).message}. Your text is kept — try again.`); } finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!reply) return;
    const actions = reply.actions.filter((_, i) => picked[i]);
    if (!actions.length) return;
    setSaving(true);
    const before = new Set((rules || []).map((r) => r.id));
    try {
      const r = await api<{ results: { description: string; ok: boolean; detail: string }[] }>("/api/v1/assistant/alerts/apply", { method: "POST", body: JSON.stringify({ actions }) });
      const failed = r.results.filter((x) => !x.ok);
      const fresh = await load();
      if (failed.length) {
        flash(false, `Not saved: ${failed.map((f) => f.detail).join("; ")}`);
      } else {
        const created = (fresh || []).filter((x) => !before.has(x.id));
        setFreshIds(created.map((x) => x.id));
        const ev = created[0]?.last_evaluation;
        flash(true, created.length
          ? `Alert saved and checked against ${ev?.eod_session ? `the ${fmtDay(ev.eod_session)} close` : "filings received so far"}: ${ev?.status === "matched" || ev?.matched.length ? "it matches now — see History" : ev?.status === "unable" ? "data was missing, so it couldn't be evaluated yet" : "no match yet"}.`
          : "Changes saved.");
        setText(""); setReply(null);
        if (actions.some((a) => a.type.endsWith("_rule"))) setTab("rules");
      }
    } catch (e) { flash(false, `Not saved: ${(e as Error).message}. Nothing was changed.`); } finally { setSaving(false); }
  };
  const toggle = async (r: AlertRule) => {
    try { await api(`/api/v1/alert-rules/${r.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !r.enabled }) }); flash(true, r.enabled ? "Alert paused." : "Alert resumed."); load(); }
    catch (e) { flash(false, (e as Error).message); }
  };
  const saveConds = async (r: AlertRule, conds: EditableCondition[]) => {
    const spec = { ...(r.spec as Record<string, unknown>) };
    const list = [...((spec.conditions as Record<string, unknown>[]) || [])];
    for (const c of conds) list[c.index] = { ...list[c.index], value: c.value };
    try {
      const out = await api<AlertRule>(`/api/v1/alert-rules/${r.id}`, { method: "PATCH", body: JSON.stringify({ spec: { ...spec, conditions: list } }) });
      flash(true, `Threshold updated and re-checked against the ${out.last_evaluation?.eod_session ? fmtDay(out.last_evaluation.eod_session) : "latest"} close.`);
      load(); return true;
    } catch (e) { flash(false, `Not saved: ${(e as Error).message}`); return false; }
  };
  const remove = async (r: AlertRule) => {
    try { await api(`/api/v1/alert-rules/${r.id}`, { method: "DELETE" }); flash(true, "Alert deleted. Its past notifications stay in History."); load(); }
    catch (e) { flash(false, (e as Error).message); }
  };
  const dismiss = async (a: AlertItem) => { await api(`/api/v1/alerts/${a.id}`, { method: "PATCH", body: JSON.stringify({ status: "dismissed" }) }); load(); };
  const checkNow = async () => {
    setChecking(true);
    try {
      const r = await api<{ new_alerts: number }>("/api/v1/alert-rules/evaluate", { method: "POST" });
      flash(true, r.new_alerts ? `${r.new_alerts} new alert${r.new_alerts > 1 ? "s" : ""} — see History.` : "Checked every active alert — nothing new matched the latest data.");
      load();
    } catch (e) { flash(false, (e as Error).message); } finally { setChecking(false); }
  };
  const refreshNow = async () => {
    setRefreshing(true);
    try {
      const r = await api<{ status: string }>("/api/v1/admin/ingest/daily", { method: "POST" });
      flash(true, r.status === "already_running" ? "A refresh is already running." : "Refreshing prices, filings and documents — this takes a few minutes.");
      setTimeout(load, 15000);
    } catch (e) { flash(false, (e as Error).message); } finally { setRefreshing(false); }
  };
  const savePrefs = (p: Partial<Prefs>) => {
    if (!prefs) return;
    setPrefs({ ...prefs, ...p });
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try { setPrefs(await api<Prefs>("/api/v1/preferences", { method: "PUT", body: JSON.stringify(p) })); flash(true, "Briefing thresholds saved."); }
      catch (e) { flash(false, `Not saved: ${(e as Error).message}`); }
    }, 500);
  };

  const single = reply?.actions.length === 1 && reply.actions[0].type === "create_rule";

  return (
    <div>
      <p className="text-[13.5px] text-ink-2">Single rules that notify you when one condition is met. FINN shows exactly how it read the request before anything is saved.</p>

      <section className="card mt-5 p-4" aria-label="Create or change alerts">
        <Steps at={step} />
        <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="mt-3">
          <label htmlFor="alert-text" className="mb-1.5 block text-[13.5px] font-medium text-ink">What should FINN watch for?</label>
          <div className="flex items-end gap-2">
            <textarea id="alert-text" ref={boxRef} value={text} onChange={(e) => setText(e.target.value)} rows={2} data-testid="alert-composer"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
              placeholder="e.g. Maruti drops more than 5%, or any CFO change"
              className="block max-h-32 min-h-[48px] w-full min-w-0 resize-none rounded-xl border border-line bg-bg px-3 py-2.5 text-[15px] outline-none focus:border-accent" />
            <button disabled={busy || text.trim().length < 2} className="btn btn-primary h-12 shrink-0">{busy ? "Reading…" : "Review"}</button>
          </div>
        </form>
        {!reply && !busy && (
          <div className="no-scrollbar -mx-4 mt-3 flex gap-2 overflow-x-auto px-4">
            {EXAMPLES.map((e) => (
              <button key={e.label} onClick={() => { setText(e.text); boxRef.current?.focus(); }} className="min-h-[36px] shrink-0 rounded-full border border-line px-3 text-[12.5px] text-ink-2 hover:border-accent hover:text-ink">{e.label}</button>
            ))}
          </div>
        )}
        {busy && <Skeleton className="mt-3 h-20" />}
        {reply && (
          <div className="mt-4 space-y-3" data-testid="assistant-reply">
            {reply.warnings && reply.warnings.length > 0 && (
              <div className="rounded-xl border border-accent/40 bg-accent/5 px-3 py-2.5 text-[13px] text-ink-2" role="alert" data-testid="unsupported-warning">
                <p className="font-medium text-ink">FINN can&apos;t watch part of this</p>
                <p className="mt-0.5">It works from end-of-day prices and exchange filings, so it can&apos;t check: {reply.warnings.join("; ")}.
                  {reply.actions.length ? " The alert below covers only what it can check." : ""}</p>
              </div>
            )}
            {reply.actions.length > 0 ? (
              <>
                <p className="label">{single ? "Review this alert" : "Review these changes"}</p>
                <ul className="space-y-2">
                  {reply.actions.map((a, i) => (
                    <li key={i} className="rounded-xl border border-line bg-surface-2/50 p-3">
                      <label className="flex cursor-pointer items-start gap-3">
                        {reply.actions.length > 1 && (
                          <input type="checkbox" className="mt-1 h-4 w-4 accent-[var(--accent)]" checked={!!picked[i]}
                            onChange={() => setPicked((p) => p.map((v, j) => (j === i ? !v : v)))} />
                        )}
                        <span className="text-[14px] font-medium leading-snug text-ink">{a.description}</span>
                      </label>
                      {a.duplicate_of && (
                        <p className="mt-2 rounded bg-accent/10 px-2 py-1 text-[12.5px] text-ink-2" data-testid="duplicate-note">
                          Same as your existing alert: “{a.duplicate_of.sentence}”. Saving it again would add nothing.
                        </p>
                      )}
                      {a.preview && <div className="mt-3 border-t border-line pt-3"><PreviewBlock p={a.preview} /></div>}
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap gap-2">
                  <button onClick={confirm} disabled={!picked.some(Boolean) || saving} className="btn btn-primary" data-testid="confirm-alert">
                    {saving ? "Saving…" : single ? "Save alert" : `Apply ${picked.filter(Boolean).length} change${picked.filter(Boolean).length === 1 ? "" : "s"}`}
                  </button>
                  <button onClick={() => { setReply(null); boxRef.current?.focus(); }} className="btn btn-quiet">Edit request</button>
                </div>
              </>
            ) : (
              <div className="rounded-xl bg-surface-2/60 px-3 py-2.5 text-[13.5px] text-ink-2">
                {reply.reply}
                <button onClick={() => setReply(null)} className="ml-2 text-[12.5px] text-info">OK</button>
              </div>
            )}
          </div>
        )}
      </section>
      {note && <p role={note.ok ? "status" : "alert"} className={`mt-3 rounded-xl border px-3 py-2 text-[13px] text-ink ${note.ok ? "border-up/30 bg-up/10" : "border-down/40 bg-down/10"}`}>{note.text}</p>}

      {err && <div className="mt-4"><ErrorState message={err} retry={load} /></div>}

      <div className="seg mt-6" role="tablist" aria-label="Alerts views">
        {([["rules", "Your alerts", rules?.length ?? 0], ["history", "History", newCount || visibleAlerts.length], ["thresholds", "Briefing", null]] as const).map(([k, label, n]) => (
          <button key={k} role="tab" aria-selected={activeTab === k} onClick={() => setTab(k)}
            >
            {label}{n !== null && n > 0 ? <span className={`ml-1.5 rounded-full px-1.5 text-[11px] ${k === "history" && newCount ? "bg-accent text-accent-ink" : "bg-line text-ink-2"}`}>{n}</span> : null}
          </button>
        ))}
      </div>

      {!rules && !err && <div className="mt-4 space-y-3"><Skeleton className="h-28" /><Skeleton className="h-28" /></div>}

      {rules && activeTab === "rules" && (
        <section className="mt-4" aria-label="Your alerts">
          <div className="mb-3 flex items-center justify-between gap-3 text-[12.5px] text-ink-3" data-testid="last-evaluated">
            <span>{evalStatus?.last_run ? <>Last checked {ago(evalStatus.last_run.at)} ({fmtTime(evalStatus.last_run.at)}) · {fmtDay(evalStatus.last_run.eod_session)} close</> : "Not checked yet"}
              {status?.scheduler?.enabled && status.scheduler.next_run ? <> · next {fmtTime(status.scheduler.next_run)}</> : null}</span>
            <button onClick={checkNow} disabled={checking} className="btn btn-secondary min-h-[36px] shrink-0" data-testid="evaluate-now">{checking ? "Checking…" : "Check now"}</button>
          </div>
          {rules.length === 0 ? (
            <EmptyState title="No saved alerts yet" body="Describe one above — for example “Reliance falls 4%” or “any CEO or CFO change”. You'll review it before it's saved." />
          ) : (
            <ul className="space-y-3">{[...rules].sort((a, b) => Number(freshIds.includes(b.id)) - Number(freshIds.includes(a.id))).map((r) => <RuleCard key={r.id} r={r} fresh={freshIds.includes(r.id)} onToggle={() => toggle(r)} onDelete={() => remove(r)} onSave={(c) => saveConds(r, c)} />)}</ul>
          )}
        </section>
      )}

      {rules && activeTab === "history" && (
        <section className="mt-4" aria-label="Alert history">
          {visibleAlerts.length === 0 ? (
            <EmptyState title="No alerts have fired" body={`${evalStatus?.last_run ? `Last checked ${ago(evalStatus.last_run.at)} against the ${fmtDay(evalStatus.last_run.eod_session)} close.` : "FINN checks after every refresh."} Notifications appear here with the rule that raised them.`}
              action={rules.length === 0 ? <button onClick={() => { setTab("rules"); boxRef.current?.focus(); }} className="btn btn-secondary">Create your first alert</button> : undefined} />
          ) : (
            <ul className="space-y-3">{visibleAlerts.map((a) => <AlertCard key={a.id} a={a} rule={a.rule_id ? ruleById.get(a.rule_id) : undefined} onDismiss={() => dismiss(a)} />)}</ul>
          )}
        </section>
      )}

      {prefs && activeTab === "thresholds" && (
        <section className="mt-4 space-y-4" aria-label="Briefing thresholds">
          <p className="text-[12.5px] leading-relaxed text-ink-3">These settings don&apos;t create alerts. They decide what the Briefing and Portfolio flag as unusual. Each saved alert keeps its own threshold.</p>
          <div className="card p-4">
            <p className="text-[14px] font-semibold">What counts as unusual</p>
            <div className="mt-4 space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div><p className="text-[13.5px]">Daily price move</p><p className="text-[12px] text-ink-3">up or down, versus the previous close</p></div>
                <Stepper value={prefs.price_move_pct} step={0.5} min={0.5} max={20} format={(v) => `±${v}%`} onChange={(v) => savePrefs({ price_move_pct: v })} label="daily price move threshold" />
              </div>
              <div className="flex items-center justify-between gap-3">
                <div><p className="text-[13.5px]">Trading volume</p><p className="text-[12px] text-ink-3">versus the 20-session average</p></div>
                <Stepper value={prefs.volume_multiple} step={0.5} min={1} max={10} format={(v) => `${v}×`} onChange={(v) => savePrefs({ volume_multiple: v })} label="volume threshold" />
              </div>
            </div>
          </div>

          <div className="card p-4">
            <p className="text-[14px] font-semibold">Filing types you follow</p>
            <p className="mt-0.5 text-[12.5px] text-ink-3">These rank higher in your Briefing. Results, deals, leadership, legal, ratings, fund-raises and pledges are always shown.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {prefs.categories.filter((c) => c.value !== "unclassified").map((c) => {
                const on = prefs.watched_categories.includes(c.value);
                return (
                  <button key={c.value} aria-pressed={on}
                    onClick={() => savePrefs({ watched_categories: on ? prefs.watched_categories.filter((x) => x !== c.value) : [...prefs.watched_categories, c.value] })}
                    className={`min-h-[36px] rounded-full px-3 text-[12.5px] ${on ? "bg-ink text-bg" : "border border-line text-ink-2"}`}>{on ? "✓ " : ""}{c.label}</button>
                );
              })}
            </div>
          </div>

          {status && (
            <div className="card p-4" data-testid="data-status">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[14px] font-semibold">Data refresh</p>
                <button onClick={refreshNow} disabled={refreshing || status.scheduler?.running} className="btn btn-secondary min-h-[36px]" data-testid="refresh-now">
                  {status.scheduler?.running ? "Refreshing…" : refreshing ? "Starting…" : "Refresh now"}
                </button>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-3 text-[12.5px]">
                <div><dt className="text-ink-3">Last refresh</dt><dd className="text-ink">{ago(status.freshness.last_ingestion_run)}{status.freshness.last_ingestion_status && status.freshness.last_ingestion_status !== "ok" ? ` (${status.freshness.last_ingestion_status})` : ""}</dd></div>
                <div><dt className="text-ink-3">Next refresh</dt><dd className="text-ink">{status.scheduler?.enabled ? fmtTime(status.scheduler.next_run) : "external schedule"}</dd></div>
                <div><dt className="text-ink-3">Prices up to</dt><dd className="text-ink">{fmtDay(status.freshness.latest_trading_date)} close</dd></div>
                <div><dt className="text-ink-3">Schedule</dt><dd className="text-ink">{status.scheduler?.times_ist ? `${status.scheduler.times_ist} IST` : "—"}</dd></div>
              </dl>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/** Monitoring plans (Investor Handover) and, per plan, "Test this plan on past data". */
function PlansView() {
  const [plans, setPlans] = useState<MonitoringPlan[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<MonitoringPlan | null>(null);
  const [replay, setReplay] = useState<MonitoringPlan | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = useCallback(() => { setErr(null); api<MonitoringPlan[]>("/api/v1/plans").then(setPlans).catch((e) => setErr(e.message)); }, []);
  useEffect(load, [load]);
  const saved = (p: MonitoringPlan, n: string) => { setNote(n); setEditing(null); load();
    requestAnimationFrame(() => document.getElementById(`plan-${p.id}`)?.scrollIntoView({ block: "nearest" })); };
  return (
    <div className="space-y-4">
      <PlanComposer key={editing?.id ?? "new"} editing={editing} onSaved={saved} onCancel={editing ? () => setEditing(null) : undefined} />
      {note && <p role="status" className="rounded-xl border border-up/30 bg-up/10 px-3 py-2 text-[13px]">{note}</p>}
      {err && <ErrorState message={err} retry={load} />}
      {!plans && !err && <Skeleton className="h-28" />}
      {plans && <div id="plans"><PlanList plans={plans} onChange={load} onEdit={(p) => { setEditing(p); window.scrollTo({ top: 0, behavior: "smooth" }); }}
        onReplay={(p) => setReplay(p)} /></div>}
      {replay && <ReplayPanel plan={replay} onClose={() => setReplay(null)} />}
    </div>
  );
}

export default function AlertsPage() {
  const [mode, setMode] = useState<"plans" | "rules" | null>(null);
  useEffect(() => {
    try {
      const q = new URLSearchParams(window.location.search);
      if (q.get("prefill")) return setMode("rules");
      if (q.get("tab") === "plans") return setMode("plans");
    } catch { /* ignore */ }
    api<MonitoringPlan[]>("/api/v1/plans").then((p) => setMode(p.length ? "plans" : "rules")).catch(() => setMode("rules"));
  }, []);
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-[30px] font-bold tracking-tight">Alerts</h1>
      <div className="seg mt-4" role="tablist" aria-label="Alert type">
        {([["plans", "Monitoring plans"], ["rules", "Single alerts"]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={mode === k} onClick={() => setMode(k)}
            >{label}</button>
        ))}
      </div>
      <div className="mt-4">{mode === "plans" ? <PlansView /> : mode === "rules" ? <AlertRulesView /> : <Skeleton className="h-40" />}</div>
    </div>
  );
}
