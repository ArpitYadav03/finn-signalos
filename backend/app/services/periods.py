"""Indian fiscal-period helpers. FY27 = 1 Apr 2026 – 31 Mar 2027; Q1 ends June, Q4 ends March."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


@dataclass(frozen=True, order=True)
class FiscalQuarter:
    fy: int  # 2-digit-agnostic full year, e.g. 2027 for FY27
    q: int

    @property
    def label(self) -> str:
        return f"Q{self.q}FY{self.fy % 100:02d}"

    @property
    def end_date(self) -> date:
        month = {1: 6, 2: 9, 3: 12, 4: 3}[self.q]
        year = self.fy - 1 if self.q in (1, 2, 3) else self.fy
        return date(year, month, calendar.monthrange(year, month)[1])

    def shift(self, n: int) -> "FiscalQuarter":
        idx = self.fy * 4 + (self.q - 1) + n
        return FiscalQuarter(idx // 4, idx % 4 + 1)


def fiscal_year_of(d: date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def quarter_of(d: date) -> FiscalQuarter:
    fy = fiscal_year_of(d)
    q = {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[d.month]
    return FiscalQuarter(fy, q)


def fy_label(fy: int) -> str:
    return f"FY{fy % 100:02d}"


def parse_month_year(text: str) -> date | None:
    """'Sep 2026' -> 2026-09-30 (period end)."""
    m = re.match(r"^\s*([A-Za-z]{3})[a-z]*\s+(\d{4})\s*$", text)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    year = int(m.group(2))
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_fy_token(token: str, reference: date | None = None) -> int | None:
    """'FY27', 'FY2027', 'FY 27', '2026-27' -> 2027. 'this year'/'next year' resolved against `reference`."""
    t = token.strip().lower()
    # "financial year '27", "fiscal year 2027", "fiscal 27" -> FY27
    t = re.sub(r"\b(?:financial|fiscal)\s+year\s*|\bfiscal\s+(?=['’]?\d)", "fy", t)
    t = t.replace("’", "'")
    m = re.search(r"fy\s?'?(\d{2}|\d{4})\b", t)
    if m:
        y = int(m.group(1))
        return 2000 + y if y < 100 else y
    m = re.search(r"\b(20\d{2})\s*[-–/]\s*(\d{2})\b", t)
    if m:
        return int(m.group(1)) + 1
    if reference is not None:
        cur = fiscal_year_of(reference)
        if "next year" in t or "next fiscal" in t:
            return cur + 1
        if "this year" in t or "current year" in t or "this fiscal" in t:
            return cur
    return None


def same_basis(a: str | None, b: str | None) -> bool:
    return (a or "").lower() == (b or "").lower()


def to_utc_naive(dt: datetime) -> datetime:
    """Normalise an aware datetime to naive UTC for storage (UI renders in IST)."""
    if dt.tzinfo is None:
        return dt
    from datetime import timezone

    return dt.astimezone(timezone.utc).replace(tzinfo=None)
