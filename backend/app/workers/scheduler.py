"""In-process daily refresh scheduler (no extra infrastructure).

Runs the daily pipeline at fixed IST times (default 08:30 for overnight filings and 18:30 after market close) and
once shortly after startup if the last successful run is stale. One run at a time (lock); every run is recorded
in `ingestion_runs`, so the UI can show last/next refresh. In a deployment where an external cron (GitHub Actions)
drives ingestion, set SCHEDULER_ENABLED=false.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import desc, select

from app.core.config import get_settings

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
_lock = threading.Lock()
_state: dict = {"enabled": False, "next_run": None, "running": False, "last_trigger": None}


def parse_times(spec: str) -> list[time]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        h, m = part.split(":")
        out.append(time(int(h), int(m)))
    return sorted(out) or [time(18, 30)]


def next_run_after(now_ist: datetime, times: list[time]) -> datetime:
    """Next scheduled IST datetime strictly after `now_ist`."""
    for day in range(0, 8):
        d = (now_ist + timedelta(days=day)).date()
        for t in times:
            cand = datetime.combine(d, t, tzinfo=IST)
            if cand > now_ist:
                return cand
    raise RuntimeError("unreachable")


def last_successful_run_at():
    from app.core.db import SessionLocal
    from app.models import IngestionRun

    db = SessionLocal()
    try:
        r = db.scalars(select(IngestionRun).where(IngestionRun.job == "daily", IngestionRun.status.in_(["ok", "partial"]))
                       .order_by(desc(IngestionRun.started_at)).limit(1)).first()
        return r.finished_at if r else None
    finally:
        db.close()


def run_now(trigger: str) -> bool:
    """Run the daily pipeline unless one is already running. Returns False if skipped."""
    if not _lock.acquire(blocking=False):
        log.info("Daily refresh already running; skipped trigger %s", trigger)
        return False
    from app.core.db import SessionLocal
    from app.ingestion.pipeline import run_daily

    _state["running"], _state["last_trigger"] = True, trigger
    db = SessionLocal()
    try:
        log.info("Daily refresh started (%s)", trigger)
        run = run_daily(db)
        log.info("Daily refresh finished: %s %s", run.status, run.stats)
        return True
    except Exception:
        log.exception("Daily refresh failed (%s)", trigger)
        return False
    finally:
        db.close()
        _state["running"] = False
        _lock.release()


def _loop(stop: threading.Event) -> None:
    s = get_settings()
    times = parse_times(s.daily_refresh_times_ist)
    # Catch-up: if data is stale when the server starts, refresh soon (after the API is up).
    last = last_successful_run_at()
    if last is None or datetime.utcnow() - last > timedelta(hours=s.catch_up_after_hours):
        if not stop.wait(60):
            run_now("startup catch-up")
    while not stop.is_set():
        nxt = next_run_after(datetime.now(IST), times)
        _state["next_run"] = nxt.isoformat()
        while not stop.is_set() and datetime.now(IST) < nxt:
            stop.wait(min(60, max(1, (nxt - datetime.now(IST)).total_seconds())))
        if not stop.is_set():
            run_now(f"scheduled {nxt:%H:%M} IST")


_thread: threading.Thread | None = None
_stop = threading.Event()


def start() -> None:
    global _thread
    s = get_settings()
    if not s.scheduler_enabled or _thread is not None:
        return
    _state["enabled"] = True
    _state["next_run"] = next_run_after(datetime.now(IST), parse_times(s.daily_refresh_times_ist)).isoformat()
    _thread = threading.Thread(target=_loop, args=(_stop,), name="finn-daily-scheduler", daemon=True)
    _thread.start()
    log.info("Daily refresh scheduler on: %s IST", s.daily_refresh_times_ist)


def stop() -> None:
    _stop.set()


def status() -> dict:
    s = get_settings()
    return {**_state, "times_ist": s.daily_refresh_times_ist, "catch_up_after_hours": s.catch_up_after_hours}
