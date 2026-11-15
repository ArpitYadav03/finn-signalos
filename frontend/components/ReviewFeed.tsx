"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ReviewFeed as Feed, Signal } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { SignalCard } from "./SignalCard";
import { ErrorState, Skeleton } from "./ui";
import { ageLabel, dedupeAcross } from "@/lib/briefingView";

/** "Since your last review": new events, updates to events you reviewed, and developments you are watching — kept apart.
 *  Opening the page changes nothing; only "Mark reviewed" / "Watch for this" record a review. */
export function ReviewFeed() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [all, setAll] = useState(false);
  const load = useCallback(() => { setErr(null); api<Feed>("/api/v1/review").then(setFeed).catch((e) => setErr(e.message)); }, []);
  useEffect(load, [load]);

  const act = async (s: Signal, action: string, milestone?: string) => {
    if (!s.event_key) return;
    setBusy(s.event_key);
    try {
      await api(`/api/v1/review/${encodeURIComponent(s.event_key)}`, { method: "POST", body: JSON.stringify({ action, milestone }) });
      setNote({ ok: true, text: action === "watch" ? `Watching ${s.ticker} for the next evidence. This is a reminder in FINN, not an alert.`
        : `${s.ticker} marked reviewed. A later filing in this event will bring it back as an update.` });
      load();
    } catch (e) {
      setNote({ ok: false, text: `Not saved: ${(e as Error).message}` });
    } finally { setBusy(null); }
  };
  const start = async () => {
    try { await api("/api/v1/review/start", { method: "POST" }); load(); } catch (e) { setNote({ ok: false, text: (e as Error).message }); }
  };

  if (err) return <ErrorState message={err} retry={load} />;
  if (!feed) return <div className="space-y-3"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-40" /></div>;
  // One card per event in this view, in display order: updates first, then new, then watching.
  const [updated, fresh, unresolved] = dedupeAcross([feed.updated, feed.new, feed.unresolved], (s) => s.event_key || String(s.id));
  const now = Date.now();
  const total = fresh.length + updated.length + unresolved.length;
  const section = (title: string, hint: string, items: Signal[], testid: string, firstReview = false) => items.length > 0 && (
    <section className="space-y-3" aria-label={title} data-testid={testid}>
      <div><h2 className="text-[15px] font-semibold">{title} <span className="num text-ink-3">· {items.length}</span></h2>
        <p className="text-[12.5px] text-ink-3">{hint}</p></div>
      {(all ? items : items.slice(0, 6)).map((s) => (
        <div key={s.event_key || s.id} className={busy === s.event_key ? "opacity-60" : ""}>
          {firstReview && ageLabel(s.published_at, now) && <p className="mb-1 text-[11.5px] font-medium text-accent" data-testid="older-item">{ageLabel(s.published_at, now)}</p>}
          <SignalCard s={s} onReview={(a, m) => act(s, a, m)} />
        </div>
      ))}
      {!all && items.length > 6 && <button className="btn btn-quiet" onClick={() => setAll(true)}>Show all {items.length}</button>}
    </section>
  );
  return (
    <div className="space-y-5" data-testid="review-feed">
      <div>
        <h2 className="text-[22px] font-semibold leading-tight tracking-tight" data-testid="review-summary">
          {feed.first_visit ? "Start your review" : total === 0 ? "You're up to date" :
            [fresh.length && `${fresh.length} new`, updated.length && `${updated.length} updated`, unresolved.length && `${unresolved.length} unresolved`].filter(Boolean).join(" · ")}
        </h2>
        <p className="mt-1 text-[14px] text-ink-2">
          {feed.first_visit ? `The most material developments for your holdings over the last ${feed.window_days} days.`
            : `Since your last review${feed.last_review_at ? ` (${fmtTime(feed.last_review_at)})` : ""}${feed.reviewed_without_changes ? ` · ${feed.reviewed_without_changes} reviewed event${feed.reviewed_without_changes === 1 ? "" : "s"} unchanged` : ""}.`}
        </p>
      </div>
      {feed.first_visit && (
        <div className="rounded-xl border border-line bg-surface-2/50 p-3 text-[13px] text-ink-2" data-testid="first-visit">
          <p>FINN tracks what you have reviewed, not what you have scrolled past. Mark an event reviewed once you have understood it;
            if a later filing changes it, it comes back as an update. Opening this page marks nothing.</p>
          <button className="btn btn-secondary mt-2" onClick={start}>Start tracking from now</button>
        </div>
      )}
      {note && <p role={note.ok ? "status" : "alert"} className={`rounded-xl border px-3 py-2 text-[13px] ${note.ok ? "border-up/30 bg-up/10" : "border-down/40 bg-down/10"}`}>{note.text}</p>}
      {section("Updated since your review", "Events you reviewed that have a new filing — what changed is shown first.", updated, "review-updated")}
      {section(feed.first_visit ? "To review" : "New", `Material events from the last ${feed.window_days} days you haven't reviewed yet. Items older than 24 hours are labelled.`, fresh, "review-new", true)}
      {section("Unresolved — watching", "Events waiting on the next evidence you chose to watch.", unresolved, "review-unresolved")}
      {!feed.first_visit && total === 0 && (
        <p className="card px-4 py-6 text-center text-[13px] text-ink-3">Nothing new or changed among your holdings&apos; material events in the last {feed.window_days} days.</p>
      )}
    </div>
  );
}
