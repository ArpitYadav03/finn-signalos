"""Deterministic financial calculations. The LLM never does arithmetic: every number shown comes from here.

Each function returns a `Calc` with the value (or None) and a reason when the value is not meaningful,
so the UI can say *why* a number is missing instead of printing a misleading one.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

UNIT_TO_CRORE = {
    "inr": 1e-7,
    "rupees": 1e-7,
    "rs": 1e-7,
    "thousand": 1e-4,
    "lakh": 1e-2,
    "lakhs": 1e-2,
    "million": 0.1,
    "mn": 0.1,
    "crore": 1.0,
    "crores": 1.0,
    "cr": 1.0,
    "billion": 100.0,
    "bn": 100.0,
}


@dataclass(frozen=True)
class Calc:
    value: float | None
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None

    def as_dict(self) -> dict:
        return {"value": None if self.value is None else round(self.value, 2), "note": self.note}


def to_crore(value: float | None, unit: str) -> float | None:
    """Normalise a monetary amount to INR crore (Indian filings mix lakh, crore and million)."""
    if value is None:
        return None
    factor = UNIT_TO_CRORE.get(unit.strip().lower())
    if factor is None:
        raise ValueError(f"Unknown monetary unit: {unit!r}")
    return value * factor


def growth_pct(current: float | None, base: float | None) -> Calc:
    """Generic growth: (current - base) / |base| * 100.

    abs() on the denominator keeps the sign meaningful when the base is negative (e.g. a loss shrinking
    from -100 to -50 is +50%), but crossings between profit and loss are flagged as not comparable.
    """
    if current is None or base is None:
        return Calc(None, "missing value")
    if base == 0:
        return Calc(None, "zero base")
    if (base < 0) != (current < 0) and current != 0:
        label = "turned profitable" if current > 0 else "turned loss-making"
        return Calc(None, f"sign change ({label})")
    return Calc((current - base) / abs(base) * 100.0)


def qoq_pct(current: float | None, previous_quarter: float | None) -> Calc:
    return growth_pct(current, previous_quarter)


def yoy_pct(current: float | None, same_quarter_last_year: float | None) -> Calc:
    return growth_pct(current, same_quarter_last_year)


def margin_pct(numerator: float | None, revenue: float | None) -> Calc:
    if numerator is None or revenue is None:
        return Calc(None, "missing value")
    if revenue <= 0:
        return Calc(None, "non-positive revenue")
    return Calc(numerator / revenue * 100.0)


def free_cash_flow(cfo: float | None, capex: float | None) -> Calc:
    """FCF = CFO - capex. Capex is accepted as a positive outflow or a negative cash-flow line."""
    if cfo is None or capex is None:
        return Calc(None, "missing value")
    return Calc(cfo - abs(capex))


def ratio(numerator: float | None, denominator: float | None, *, allow_negative_denominator: bool = False) -> Calc:
    if numerator is None or denominator is None:
        return Calc(None, "missing value")
    if denominator == 0:
        return Calc(None, "zero denominator")
    if denominator < 0 and not allow_negative_denominator:
        return Calc(None, "negative denominator")
    return Calc(numerator / denominator)


def debt_to_equity(total_debt: float | None, equity: float | None) -> Calc:
    c = ratio(total_debt, equity)
    if c.note == "negative denominator":
        return Calc(None, "negative net worth")
    return c


def cagr_pct(start: float | None, end: float | None, years: float) -> Calc:
    if start is None or end is None:
        return Calc(None, "missing value")
    if years <= 0:
        return Calc(None, "invalid period")
    if start <= 0 or end <= 0:
        return Calc(None, "CAGR undefined for non-positive values")
    return Calc(((end / start) ** (1.0 / years) - 1.0) * 100.0)


def cash_conversion(cfo: float | None, pat: float | None) -> Calc:
    """CFO / PAT. Values persistently below ~0.8 suggest profits are not turning into cash."""
    if pat is not None and pat <= 0:
        return Calc(None, "PAT non-positive")
    return ratio(cfo, pat)


def daily_return_pct(close_today: float | None, close_previous: float | None) -> Calc:
    if close_today is None or close_previous is None:
        return Calc(None, "missing close")
    if close_previous <= 0:
        return Calc(None, "invalid previous close")
    return Calc((close_today - close_previous) / close_previous * 100.0)


def volume_ratio(volume_today: float | None, previous_volumes: Sequence[float | None], window: int = 20) -> Calc:
    """volume_today / mean(previous `window` sessions). Needs `window` prior sessions of history (fewer = insufficient
    history, e.g. a newly listed or newly tracked stock); within that window, at least 80% must carry a volume."""
    if volume_today is None:
        return Calc(None, "missing volume")
    sessions = list(previous_volumes)[-window:]
    if len(sessions) < window:
        return Calc(None, f"insufficient history ({len(sessions)} of {window} prior sessions)")
    hist = [v for v in sessions if v is not None and v > 0]
    if len(hist) < max(5, int(window * 0.8)):
        return Calc(None, f"insufficient history ({len(hist)} sessions)")
    avg = sum(hist) / len(hist)
    return Calc(volume_today / avg)


def pp_diff(actual_pct: float | None, target_pct: float | None) -> Calc:
    """Difference in percentage points between two percentages (used by the promise tracker)."""
    if actual_pct is None or target_pct is None:
        return Calc(None, "missing value")
    return Calc(actual_pct - target_pct)


def cash_conversion_ebitda_pct(cfo: float | None, ebitda: float | None) -> Calc:
    """CFO ÷ EBITDA (operating profit) × 100: how much of operating profit arrived as operating cash."""
    if ebitda is not None and ebitda <= 0:
        return Calc(None, "operating profit non-positive")
    c = ratio(cfo, ebitda)
    return Calc(None, c.note) if c.value is None else Calc(c.value * 100.0)


def difference(current: float | None, base: float | None) -> Calc:
    """current − base, in the inputs' own unit (days, percentage points)."""
    if current is None or base is None:
        return Calc(None, "missing value")
    return Calc(current - base)


def median(values: Sequence[float | None]) -> Calc:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return Calc(None, "no values")
    m = len(xs) // 2
    return Calc(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2)
