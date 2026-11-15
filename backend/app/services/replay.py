"""Historical Alert Replay: "Test this plan on past data".

Runs the *production* rule evaluators (monitoring.market_check, monitoring.filing_matches, monitoring.guidance_cuts) one
trading session at a time, seeing only what existed by the close of that session:

* prices: bars up to and including the session (volume baseline = the previous 20 sessions, via market.snapshot);
* filings: published by the end of the session day (IST); event history is rebuilt from those filings only, so a later
  allotment or repeat cannot leak backwards; in "indexed" mode, also only filings FINN had ingested by then;
* guidance: statements dated on or before the session (later revisions excluded).

Disclosed, not hidden: FINN keeps one version of each document and its *current* analysis of each filing (not what it
would have said then); filings before a company's continuous-coverage date may be missing, so filing conditions are
"unable to evaluate" for those sessions. A replay never creates alerts, never changes the plan, and makes no return or
accuracy claims.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, time as dtime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, utcnow
from app.services import monitoring as M

IST = timedelta(hours=5, minutes=30)
STALE_AFTER = timedelta(seconds=90)


def _ist_day(dt: datetime) -> date:
    return (dt + IST).date()


def _day_end_utc(d: date) -> datetime:
    return datetime.combine(d, dtime(23, 59, 59)) - IST


def _scope(db: Session, spec: M.PlanSpec):
    from app.ingestion.pipeline import portfolio_companies
    from app.models import Company

    if spec.tickers:
        return db.scalars(select(Company).where(Company.ticker.in_(spec.tickers))).all()
    return portfolio_companies(db)


def coverage(db: Session, spec: M.PlanSpec, start: date, end: date) -> dict:
    """What the data can support before anything runs: sessions, per-company bars, volume readiness, filing coverage."""
    from app.models import Announcement, Price, SourceCoverage

    scope = _scope(db, spec)
    ids = [c.id for c in scope]
    # The session calendar is every date with any stored bar (FINN's view of the market calendar), so a company with no
    # bar on a trading day shows as missing data rather than silently looking like a holiday.
    sessions = sorted(set(db.scalars(select(Price.trading_date).where(Price.trading_date >= start, Price.trading_date <= end)).all()))
    calendar_days = (end - start).days + 1
    needs_market = any(c.kind in ("price_move", "volume") for c in spec.conditions)
    needs_volume = any(c.kind == "volume" for c in spec.conditions)
    needs_filings = any(c.kind in ("filing", "fundraise_update") for c in spec.conditions)
    cov = {r.company_id: r for r in db.scalars(select(SourceCoverage).where(SourceCoverage.source == "filings_listing")).all()}
    companies = []
    for c in scope:
        bars = sorted(db.scalars(select(Price.trading_date).where(Price.company_id == c.id, Price.trading_date <= end)).all())
        in_range = [b for b in bars if b >= start]
        volume_ready = bars[20] if len(bars) > 20 else None      # the 21st bar is the first with 20 prior sessions
        first_filing = db.scalar(select(func.min(Announcement.published_at)).where(Announcement.company_id == c.id))
        cs = cov.get(c.id).continuous_since if cov.get(c.id) else None
        companies.append({"ticker": c.ticker, "bars_in_range": len(in_range), "missing_sessions": [s.isoformat() for s in sessions if s not in set(in_range)],
                          "first_bar": bars[0].isoformat() if bars else None,
                          "volume_ready_from": volume_ready.isoformat() if volume_ready else None,
                          "filings_complete_from": _ist_day(cs).isoformat() if cs else None,
                          "first_filing_seen": _ist_day(first_filing).isoformat() if first_filing else None})
    needs_docs = needs_filings or any(c.kind == "guidance_cut" for c in spec.conditions)
    limits = ((["Filing classifications are FINN's current analysis of each filing, not what FINN would have produced on that date.",
                "FINN keeps only the latest version of each document; earlier versions of revised documents are not available."] if needs_docs else []) +
              (["End-of-day data only: a match means the session's close met the rule, not that an intraday alert would have fired."] if needs_market else []))
    if needs_filings:
        late = [x for x in companies if not x["filings_complete_from"] or x["filings_complete_from"] > start.isoformat()]
        if late:
            limits.append(f"Filing conditions cannot be evaluated before each company's coverage date ({len(late)} of {len(companies)} companies start "
                          "after the replay start): the filings source lists only recent filings, so earlier ones may be missing.")
    if needs_volume:
        early = [x for x in companies if not x["volume_ready_from"] or x["volume_ready_from"] > start.isoformat()]
        if early:
            limits.append(f"Volume needs 20 prior sessions: for {len(early)} compan{'y' if len(early) == 1 else 'ies'} the earliest evaluable session is "
                          f"{min((x['volume_ready_from'] for x in early if x['volume_ready_from']), default='never')}.")
    return {"sessions": [s.isoformat() for s in sessions], "session_count": len(sessions), "non_trading_days": calendar_days - len(sessions),
            "companies": companies, "limits": limits, "needs_market": needs_market, "needs_filings": needs_filings,
            "price_history": {"first": min((x["first_bar"] for x in companies if x["first_bar"]), default=None)}}


def evaluate_session(db: Session, spec: M.PlanSpec, scope, d: date, prev: date | None, *, mode: str, reported: dict) -> dict:
    """One session. `reported` (event_key → filing ids already reported) carries duplicate suppression across sessions."""
    from app.api.routes import _event_dna, _iso
    from app.models import Announcement, SourceCoverage
    from app.services.market import snapshot

    out = {"session": d.isoformat(), "matches": [], "suppressed": [], "unable": []}
    window_start = (prev + timedelta(days=1)) if prev else d          # filings on non-trading days roll into the next session
    end_utc = _day_end_utc(d)
    cov = {r.company_id: r for r in db.scalars(select(SourceCoverage).where(SourceCoverage.source == "filings_listing")).all()}
    has_filing_conds = any(c.kind in ("filing", "fundraise_update") for c in spec.conditions)
    for co in scope:
        if has_filing_conds:
            cs = cov.get(co.id).continuous_since if cov.get(co.id) else None
            if cs is None or _ist_day(cs) > d:
                out["unable"].append({"ticker": co.ticker, "kind": "filings",
                                      "reason": f"FINN's filing record is complete only from {_ist_day(cs).isoformat() if cs else 'an unknown date'}; "
                                                "earlier filings may be missing."})
            else:
                q = select(Announcement).where(Announcement.company_id == co.id, Announcement.published_at <= end_utc,
                                               Announcement.processing_status == "processed")
                if mode == "indexed":
                    q = q.where(Announcement.ingested_at <= end_utc)
                known = [a for a in db.scalars(q).all() if a.analysis]
                groups: dict[str, list] = {}
                for a in known:
                    groups.setdefault(a.event_group or f"solo{a.id}", []).append(a)
                for key, members in groups.items():
                    # When did this filing become available? Publicly: its publication time. "Indexed" mode: when FINN had it.
                    avail = (lambda m: _ist_day(max(m.published_at, m.ingested_at or m.published_at))) if mode == "indexed" \
                        else (lambda m: _ist_day(m.published_at))
                    fresh = [m for m in members if window_start <= avail(m) <= d]
                    if not fresh:
                        continue
                    why = M.filing_matches(spec, members)
                    if not why:
                        continue
                    dna = {x["id"]: x for x in _event_dna(members)}       # roles from filings available by this session only
                    seen = reported.get(key, set())
                    new = [dna[m.id] for m in sorted(fresh, key=lambda m: m.published_at) if m.id not in seen]
                    if not new:
                        continue
                    meaningful = [x for x in new if x["role"] not in M.REPEAT_ROLES]
                    item = {"ticker": co.ticker, "event_key": key, "matched": why, "filings": new,
                            "indexed_later": [x["id"] for x in new if (m := next(a for a in members if a.id == x["id"])).ingested_at
                                              and m.ingested_at > end_utc],
                            "headline": next(a for a in members if a.id == new[-1]["id"]).analysis.headline}
                    if not meaningful:
                        out["suppressed"].append({**item, "reason": "repeat of a disclosure already reported in this replay"})
                    else:
                        out["matches"].append({**item, "kind": "update" if seen else "new"})
                    reported[key] = seen | {m.id for m in fresh}
        if any(c.kind == "guidance_cut" for c in spec.conditions):
            for gc in M.guidance_cuts(db, co, datetime.combine(window_start, dtime()), as_of=d):
                if gc["date"] <= d.isoformat():
                    out["matches"].append({"ticker": co.ticker, "kind": "new", "matched": [M.describe(M.PlanCondition(kind="guidance_cut"))],
                                           "guidance_cut": {k: gc[k] for k in ("metric", "period", "basis", "previous", "current", "previous_date", "date")}})
        if any(c.kind in ("price_move", "volume") for c in spec.conditions):
            s = snapshot(db, co.id, as_of=d)
            if s is None or s["trading_date"] != d.isoformat():
                out["unable"].append({"ticker": co.ticker, "kind": "market",
                                      "reason": f"No end-of-day bar for {d.isoformat()} (missing data); FINN does not reuse the previous close."})
            else:
                m = M.market_check(spec, s)
                for u in m["unable"]:
                    out["unable"].append({"ticker": co.ticker, "kind": "market", "reason": f"“{u['condition']}”: {u['reason']}"})
                if m["result"] == "match":
                    out["matches"].append({"ticker": co.ticker, "kind": "new", "matched": m["matched"], "values": m["values"],
                                           "close": s["close"], "previous_close": s["previous_close"]})
    out["evaluated_at"] = _iso(utcnow())
    return out


# ------------------------------------------------------------------ durable runner

def _claim(db: Session, run_id: int) -> bool:
    """Atomically move queued/interrupted → running; a second worker or a double click gets False."""
    from app.models import ReplayRun

    res = db.execute(update(ReplayRun).where(ReplayRun.id == run_id, ReplayRun.status.in_(("queued", "interrupted")))
                     .values(status="running", started_at=utcnow(), heartbeat_at=utcnow(), cancel_requested=False))
    db.commit()
    return res.rowcount == 1


def execute(run_id: int) -> None:
    """Run (or resume) a replay in its own session. Saves after every session; honours cancel between sessions."""
    from app.models import ReplayRun

    db = SessionLocal()
    try:
        if not _claim(db, run_id):
            return
        run = db.get(ReplayRun, run_id)
        spec = M.PlanSpec.model_validate(run.spec)
        scope = _scope(db, spec)
        sessions = [date.fromisoformat(x) for x in run.coverage.get("sessions", [])]
        done = {r["session"] for r in run.results or []}
        from app.models import Price

        prev_all = sorted(set(db.scalars(select(Price.trading_date)).all()))
        reported: dict[str, set] = {}
        for r in run.results or []:      # rebuild duplicate-suppression state when resuming
            for m in r.get("matches", []) + r.get("suppressed", []):
                if m.get("event_key"):
                    reported.setdefault(m["event_key"], set()).update(f["id"] for f in m.get("filings", []))
        for d in sessions:
            db.refresh(run)
            if run.cancel_requested:
                run.status, run.finished_at = "cancelled", utcnow()
                db.commit()
                return
            if d.isoformat() in done:
                continue
            prev = max((x for x in prev_all if x < d), default=None)
            res = evaluate_session(db, spec, scope, d, prev, mode=run.mode, reported=reported)
            run.results = [*(run.results or []), res]
            run.progress = {"done": len(run.results), "total": len(sessions), "last_session": d.isoformat()}
            run.heartbeat_at = utcnow()
            db.commit()
        run.status, run.finished_at = "succeeded", utcnow()
        db.commit()
    except Exception as e:  # recorded on the run; partial results are kept for inspection or resume
        db.rollback()
        run = db.get(ReplayRun, run_id)
        if run is not None:
            run.status, run.error, run.finished_at = "failed", str(e)[:500], utcnow()
            db.commit()
        raise
    finally:
        db.close()


def start(run_id: int) -> None:
    threading.Thread(target=execute, args=(run_id,), daemon=True, name=f"replay-{run_id}").start()


def recover_stale(db: Session, *, at_startup: bool = False) -> int:
    """A run whose worker died (restart, crash) is marked interrupted so it can be resumed, never left 'running'.
    At startup every 'running' run is orphaned: no worker of this process can own it yet."""
    from app.models import ReplayRun

    q = update(ReplayRun).where(ReplayRun.status == "running")
    if not at_startup:
        q = q.where(ReplayRun.heartbeat_at < utcnow() - STALE_AFTER)
    res = db.execute(q.values(status="interrupted"))
    db.commit()
    return res.rowcount


def summary(run) -> dict:
    res = run.results or []
    return {"sessions_evaluated": len(res), "matches": sum(len(r["matches"]) for r in res),
            "sessions_with_matches": sum(1 for r in res if r["matches"]), "suppressed": sum(len(r["suppressed"]) for r in res),
            "unable": sum(len(r["unable"]) for r in res), "sessions_without_matches": sum(1 for r in res if not r["matches"])}
