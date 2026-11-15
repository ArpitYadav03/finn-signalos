"""Versioned financial data: every source fetch is a snapshot; values are append-only observations.

Flow for a new fetch:
1. Hash the parsed values. Same hash as the latest promoted snapshot → only ``last_seen_at`` moves.
2. Validate the candidate against the current values (basis, completeness, value sanity, mass restatement).
3. Rejected → recorded with the reasons; the serving table is untouched.
4. Promoted → new or changed values become new observations (a changed value supersedes the old one, which stays
   in history as a restatement); ``financial_metrics`` (what the app reads) is updated row by row from current
   observations — no wholesale delete.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Company, FinancialMetric, FinancialObservation, FinancialSnapshot

EXTRACTION_VERSION = "screener-parser-v1"
UNITS = {"crore", "pct", "days", "rs", "x", "ratio", "count"}
NON_NEGATIVE = {"revenue", "interest", "equity_capital", "borrowings"}


@dataclass
class Candidate:
    fiscal_period: str
    period_end: object
    period_type: str
    metric_name: str
    value: float | None
    unit: str


@dataclass
class Validation:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def _key(period: str, ptype: str, basis: str, metric: str) -> tuple:
    return (period, ptype, basis, metric)


def source_hash(basis: str, rows: list[Candidate]) -> str:
    payload = sorted([r.fiscal_period, r.period_type, r.metric_name, None if r.value is None else round(r.value, 6), r.unit]
                     for r in rows)
    return hashlib.sha256(json.dumps([basis, payload], default=str).encode()).hexdigest()


def validate(rows: list[Candidate], basis: str | None, current: dict[tuple, float | None], current_basis: str | None) -> Validation:
    v = Validation()
    if not rows:
        v.errors.append("Snapshot has no values.")
    if not basis:
        v.errors.append("Statement basis (consolidated/standalone) missing.")
    if current_basis and basis and basis != current_basis:
        v.errors.append(f"Statement basis changed from {current_basis} to {basis}; not mixing bases silently.")
    bad_units = sorted({r.unit for r in rows if r.unit not in UNITS})
    if bad_units:
        v.errors.append(f"Unknown units: {bad_units}")
    nonfinite = [r for r in rows if r.value is not None and not math.isfinite(r.value)]
    if nonfinite:
        v.errors.append(f"{len(nonfinite)} non-finite values")
    neg = [f"{r.metric_name} {r.fiscal_period}" for r in rows if r.metric_name in NON_NEGATIVE and r.value is not None and r.value < 0]
    if neg:
        v.errors.append(f"Negative values where impossible: {neg[:5]}")
    import re
    badp = [r.fiscal_period for r in rows if not re.fullmatch(r"(Q[1-4]|H[12])?FY\d{2}", r.fiscal_period or "")]
    if badp:
        v.errors.append(f"Unrecognised period labels: {sorted(set(badp))[:5]}")
    if current:
        new_keys = {_key(r.fiscal_period, r.period_type, basis or "", r.metric_name) for r in rows}
        cur_keys = set(current)
        cur_q = {k[0] for k in cur_keys if k[1] == "quarter"}
        new_q = {k[0] for k in new_keys if k[1] == "quarter"}
        if cur_q and len(new_q) < 0.5 * len(cur_q):
            v.errors.append(f"Quarterly history shrank from {len(cur_q)} to {len(new_q)} periods (likely a partial page).")
        cur_metrics, new_metrics = {k[3] for k in cur_keys}, {k[3] for k in new_keys}
        lost = cur_metrics - new_metrics
        if {"revenue", "pat"} & lost:
            v.errors.append(f"Core metrics missing from the new snapshot: {sorted({'revenue', 'pat'} & lost)}")
        elif lost:
            v.warnings.append(f"Metrics no longer reported: {sorted(lost)[:6]}")
        overlap = [(k, current[k], r.value) for r in rows
                   for k in [_key(r.fiscal_period, r.period_type, basis or "", r.metric_name)] if k in current]
        big = [o for o in overlap if o[1] not in (None, 0) and o[2] is not None and abs(o[2] - o[1]) / abs(o[1]) > 0.2]
        if overlap and len(big) > 0.4 * len(overlap) and len(big) >= 10:
            v.errors.append(f"{len(big)} of {len(overlap)} existing values changed by more than 20% at once — "
                            "looks like a parsing or basis error, not a restatement.")
        elif big:
            v.warnings.append(f"{len(big)} value(s) restated by more than 20%.")
    v.ok = not v.errors
    return v


def record_snapshot(db: Session, company: Company, rows: list[Candidate], *, basis: str | None, source: str,
                    source_url: str | None, extraction_version: str = EXTRACTION_VERSION) -> FinancialSnapshot:
    # dedupe repeated (period, type, metric) rows from the parser: first wins, as before
    seen, uniq = set(), []
    for r in rows:
        k = (r.fiscal_period, r.period_type, r.metric_name)
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    rows = uniq
    h = source_hash(basis or "", rows)
    last = db.scalars(select(FinancialSnapshot).where(FinancialSnapshot.company_id == company.id,
                                                      FinancialSnapshot.status == "promoted")
                      .order_by(FinancialSnapshot.id.desc())).first()
    now = utcnow()
    if last is not None and last.source_hash == h and last.extraction_version == extraction_version:
        last.last_seen_at = now
        db.commit()
        return last
    current_obs = {(o.fiscal_period, o.period_type, o.statement_basis, o.metric_name): o for o in db.scalars(
        select(FinancialObservation).where(FinancialObservation.company_id == company.id, FinancialObservation.is_current.is_(True))).all()}
    if current_obs:
        current = {k: o.value for k, o in current_obs.items()}
        current_basis = next(iter(current_obs.values())).statement_basis
    else:  # first versioned fetch: validate against what the app currently serves
        fm = db.scalars(select(FinancialMetric).where(FinancialMetric.company_id == company.id)).all()
        current = {(m.fiscal_period, m.period_type, m.statement_basis, m.metric_name): m.metric_value for m in fm}
        current_basis = fm[0].statement_basis if fm else None
    val = validate(rows, basis, current, current_basis)
    snap = FinancialSnapshot(company_id=company.id, source=source, source_url=source_url, source_hash=h,
                             extraction_version=extraction_version, statement_basis=basis, observed_at=now, last_seen_at=now,
                             status="promoted" if val.ok else "rejected", n_values=len(rows), validation=val.as_dict(), changes={})
    db.add(snap)
    db.flush()
    if not val.ok:
        db.commit()
        return snap
    changes = {"new": 0, "restated": 0, "unchanged": 0, "dropped": 0}
    new_keys = set()
    for r in rows:
        k = _key(r.fiscal_period, r.period_type, basis, r.metric_name)
        new_keys.add(k)
        prev = current_obs.get(k)
        same = prev is not None and ((prev.value is None and r.value is None) or
                                     (prev.value is not None and r.value is not None and abs(prev.value - r.value) < 1e-9))
        if same:
            changes["unchanged"] += 1
            continue
        obs = FinancialObservation(company_id=company.id, snapshot_id=snap.id, fiscal_period=r.fiscal_period, period_end=r.period_end,
                                   period_type=r.period_type, statement_basis=basis, metric_name=r.metric_name, value=r.value,
                                   unit=r.unit, observed_at=now, is_current=True, supersedes_id=prev.id if prev else None)
        if prev is not None:
            prev.is_current = False
            changes["restated"] += 1
        else:
            changes["new"] += 1
        db.add(obs)
    # Periods that rolled out of the source's window stay current: absence from a rolling window is not a restatement.
    changes["dropped"] = len(set(current_obs) - new_keys)
    snap.changes = changes
    db.flush()
    _project(db, company, source, source_url)
    db.commit()
    return snap


def _project(db: Session, company: Company, source: str, source_url: str | None) -> None:
    """Make financial_metrics (what the app reads) equal to the current observations, row by row."""
    cur = db.scalars(select(FinancialObservation).where(FinancialObservation.company_id == company.id,
                                                        FinancialObservation.is_current.is_(True))).all()
    want = {(o.fiscal_period, o.period_type, o.statement_basis, o.metric_name): o for o in cur}
    have = {(m.fiscal_period, m.period_type, m.statement_basis, m.metric_name): m for m in
            db.scalars(select(FinancialMetric).where(FinancialMetric.company_id == company.id)).all()}
    for k, m in have.items():
        if k not in want:
            db.delete(m)
    for k, o in want.items():
        m = have.get(k)
        if m is None:
            db.add(FinancialMetric(company_id=company.id, fiscal_period=o.fiscal_period, period_end=o.period_end, period_type=o.period_type,
                                   statement_basis=o.statement_basis, metric_name=o.metric_name, metric_value=o.value, unit=o.unit,
                                   source=source, source_url=source_url, fetched_at=o.observed_at))
        elif m.metric_value != o.value or m.unit != o.unit:
            m.metric_value, m.unit, m.fetched_at, m.source_url = o.value, o.unit, o.observed_at, source_url


def history(db: Session, company_id: int, metric: str | None = None, period: str | None = None) -> list[dict]:
    q = select(FinancialObservation, FinancialSnapshot).join(FinancialSnapshot, FinancialSnapshot.id == FinancialObservation.snapshot_id) \
        .where(FinancialObservation.company_id == company_id)
    if metric:
        q = q.where(FinancialObservation.metric_name == metric)
    if period:
        q = q.where(FinancialObservation.fiscal_period == period)
    out = []
    for o, s in db.execute(q.order_by(FinancialObservation.metric_name, FinancialObservation.fiscal_period, FinancialObservation.id)).all():
        out.append({"metric": o.metric_name, "period": o.fiscal_period, "period_type": o.period_type, "basis": o.statement_basis,
                    "value": o.value, "unit": o.unit, "observed_at": o.observed_at.isoformat() + "Z", "current": o.is_current,
                    "supersedes": o.supersedes_id, "observation_id": o.id, "snapshot_id": s.id, "source": s.source,
                    "source_hash": s.source_hash[:12], "extraction_version": s.extraction_version})
    return out


def snapshots(db: Session, company_id: int, limit: int = 10) -> list[dict]:
    rows = db.scalars(select(FinancialSnapshot).where(FinancialSnapshot.company_id == company_id)
                      .order_by(FinancialSnapshot.id.desc()).limit(limit)).all()
    return [{"id": s.id, "status": s.status, "observed_at": s.observed_at.isoformat() + "Z", "last_seen_at": s.last_seen_at.isoformat() + "Z",
             "source": s.source, "source_hash": s.source_hash[:12], "extraction_version": s.extraction_version, "basis": s.statement_basis,
             "n_values": s.n_values, "changes": s.changes, "validation": s.validation} for s in rows]
