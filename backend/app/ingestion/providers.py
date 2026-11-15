"""Replaceable source adapters. Business logic depends only on these interfaces."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from app.ingestion.http import FetchError, get_text
from app.ingestion.screener_parser import ParsedAnnouncement, ParsedCompany, parse_company_page

log = logging.getLogger(__name__)
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


def normalise_ticker(raw: str) -> str:
    t = raw.strip().upper().removesuffix(".NS").removesuffix(".BO")
    if not TICKER_RE.match(t):
        raise ValueError(f"Invalid NSE symbol: {raw!r}")
    return t


@dataclass
class Bar:
    trading_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


class MarketDataProvider(Protocol):
    name: str

    def fetch_eod(self, tickers: list[str], days: int = 60) -> dict[str, list[Bar]]: ...


class CompanyDataProvider(Protocol):
    """Company profile + document index + announcements + standardised filed results."""

    name: str

    def fetch_company(self, ticker: str, *, force: bool = False) -> tuple[ParsedCompany, str, bool]: ...


class YahooMarketProvider:
    """End-of-day prices via yfinance (NSE symbols suffixed .NS). Close is the unadjusted close."""

    name = "yahoo_finance"

    def fetch_eod(self, tickers: list[str], days: int = 60) -> dict[str, list[Bar]]:
        import math
        import warnings

        import yfinance as yf

        warnings.filterwarnings("ignore")
        symbols = [f"{t}.NS" for t in tickers]
        out: dict[str, list[Bar]] = {t: [] for t in tickers}
        try:
            df = yf.download(" ".join(symbols), period=f"{max(days, 30)}d", interval="1d", group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
        except Exception as e:  # yfinance raises a variety of errors on outages
            log.error("yfinance download failed: %s", e)
            return out
        if df is None or df.empty:
            return out
        for t, sym in zip(tickers, symbols):
            try:
                sub = df[sym] if len(symbols) > 1 else df
            except KeyError:
                continue
            for idx, row in sub.iterrows():
                close = row.get("Close")
                if close is None or (isinstance(close, float) and math.isnan(close)):
                    continue  # holiday / missing session for this symbol

                def f(v):
                    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

                out[t].append(Bar(idx.date(), f(row.get("Open")), f(row.get("High")), f(row.get("Low")),
                                  float(close), f(row.get("Volume"))))
        return out


class ScreenerCompanyProvider:
    """Screener.in company page: links to primary filings (BSE PDFs, ARs, transcripts) + filed results table."""

    name = "screener"

    def fetch_company(self, ticker: str, *, force: bool = False, cache_hours: float | None = None) -> tuple[ParsedCompany, str, bool]:
        ticker = normalise_ticker(ticker)
        last_err: Exception | None = None
        for basis in ("consolidated/", ""):
            url = f"https://www.screener.in/company/{ticker}/{basis}"
            try:
                html, cached = get_text(url, cache_hours=0 if force else cache_hours)
            except FetchError as e:
                last_err = e
                continue
            parsed = parse_company_page(html)
            # Some companies have no consolidated accounts: Screener then shows an empty consolidated table.
            if basis and not any(m.metric_name == "revenue" and m.value is not None for m in parsed.metrics):
                continue
            return parsed, url, cached
        raise FetchError(f"Company {ticker} not found on document index ({last_err})")


class FixtureAnnouncementProvider:
    """Imports genuine filings saved as JSON (for offline demos). Rows are flagged is_sample in the DB."""

    name = "fixture"

    def __init__(self, path: Path):
        self.path = path

    def fetch(self, ticker: str) -> list[ParsedAnnouncement]:
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text())
        return [
            ParsedAnnouncement(r["title"], r["url"], datetime.fromisoformat(r["published_at"]), r.get("summary"))
            for r in rows
            if r["ticker"].upper() == ticker.upper()
        ]
