"""Market snapshot calculations over stored EOD bars (deterministic)."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Price
from app.services import finance


def last_expected_session(today: date) -> date:
    """Most recent weekday on/before today (exchange holidays are not modelled — staleness is a soft hint)."""
    d = today
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def snapshot(db: Session, company_id: int, *, as_of: date | None = None, window: int = 20) -> dict | None:
    q = select(Price).where(Price.company_id == company_id)
    if as_of:
        q = q.where(Price.trading_date <= as_of)
    bars = db.scalars(q.order_by(Price.trading_date.desc()).limit(window + 2)).all()
    if not bars:
        return None
    bars = list(reversed(bars))
    last = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else None
    ret = finance.daily_return_pct(last.close, prev.close) if prev else finance.Calc(None, "no previous session stored")
    vr = finance.volume_ratio(last.volume, [b.volume for b in bars[:-1]], window)
    # A duplicated bar (open, high, low AND close identical to the previous session) means the provider repeated
    # yesterday's data: the move is unknown. An unchanged close with its own open/high/low is a genuine 0.0% day.
    def same(a, b):
        return a is not None and b is not None and abs(a - b) < 1e-6
    duplicate = bool(prev and all(same(getattr(last, f), getattr(prev, f)) for f in ("open", "high", "low", "close")))
    behind = (as_of is None) and last.trading_date < last_expected_session(date.today()) - timedelta(days=3)
    stale = duplicate
    if duplicate:
        ret = finance.Calc(None, "provider repeated the previous session's bar — move unknown until it updates")
    first = bars[-window] if len(bars) >= window else bars[0]
    trend = finance.growth_pct(last.close, first.close)
    return {
        "trading_date": last.trading_date.isoformat(),
        "close": last.close,
        "previous_close": prev.close if prev else None,
        "return_pct": ret.as_dict(),
        "volume": last.volume,
        "volume_ratio": vr.as_dict(),
        "source": last.source,
        "fetched_at": last.fetched_at.isoformat() + "Z",
        "sparkline": [b.close for b in bars[-window:]],
        "trend_pct": trend.as_dict()["value"],  # change over the sparkline window (not today's move)
        "stale_suspect": stale,
        "behind_schedule": bool(behind),  # latest bar is several sessions old: data not updating
    }


def bars_around(db: Session, company_id: int, day: date) -> dict | None:
    """Market reaction on a filing date (or the next session if filed after hours/holiday)."""
    nxt = db.scalars(
        select(Price).where(Price.company_id == company_id, Price.trading_date >= day).order_by(Price.trading_date).limit(1)
    ).first()
    if not nxt:
        return None
    return snapshot(db, company_id, as_of=nxt.trading_date)


# ------------------------------------------------------------------ event timing vs trading sessions

SESSION_OPEN, SESSION_CLOSE = (9, 15), (15, 30)   # NSE cash market, IST


def event_timing(db: Session, company_id: int, published_at_utc) -> dict:
    """Place a filing relative to trading sessions (IST), so market context before the event is never presented as a
    reaction to it.

    * pre_event: the last close completed before publication — context, not reaction.
    * reaction: the first session that traded entirely after publication. A filing made during trading hours splits
      that day's bar, so the day is reported as "mixed" and the next session is the first clean one.
    The calendar is the company's own stored bars; weekends are never sessions. Nothing here implies causation."""
    from datetime import timedelta as _td

    pub = published_at_utc + _td(hours=5, minutes=30)
    d, hm = pub.date(), (pub.hour, pub.minute)
    bars = db.scalars(select(Price).where(Price.company_id == company_id).order_by(Price.trading_date)).all()
    dates = [b.trading_date for b in bars]
    by_date = {b.trading_date: b for b in bars}
    last_known = dates[-1] if dates else None
    is_session = d in by_date or (d.weekday() < 5 and (last_known is None or d > last_known))
    if not is_session:
        relation = "non_trading_day"
    elif hm < SESSION_OPEN:
        relation = "before_open"
    elif hm < SESSION_CLOSE:
        relation = "during_session"
    else:
        relation = "after_close"
    pre_cut = d if relation == "after_close" else d - _td(days=1)
    pre = next((b for b in reversed(bars) if b.trading_date <= pre_cut), None)
    if relation == "before_open":
        clean = next((x for x in dates if x >= d), None)
    else:
        clean = next((x for x in dates if x > d), None)
    mixed = by_date.get(d) if relation == "during_session" else None

    def move(day):
        if day is None:
            return None
        s = snapshot(db, company_id, as_of=day)
        return None if s is None else {"date": day.isoformat(), "return_pct": s["return_pct"]["value"],
                                        "note": s["return_pct"]["note"], "close": s["close"], "volume_ratio": s["volume_ratio"]["value"]}

    reaction = move(clean)
    words = {"before_open": "before the market opened", "during_session": "during trading hours",
             "after_close": "after the market closed", "non_trading_day": "on a non-trading day"}[relation]
    if reaction:
        label = f"Filed {words}; first full session after it: {reaction['date']}"
    elif relation == "during_session":
        label = f"Filed {words}; no complete session after it has traded yet"
    else:
        label = f"Filed {words}; the next session has not traded yet"
    return {
        "relation": relation, "published_ist": pub.isoformat(timespec="minutes"),
        "pre_event": {"date": pre.trading_date.isoformat(), "close": pre.close} if pre else None,
        "mixed_session": move(mixed.trading_date) if mixed else None,
        "reaction": reaction, "label": label,
        "caution": "Timing only: a move after a filing is not evidence that the filing caused it.",
    }
