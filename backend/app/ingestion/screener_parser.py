"""Parser for a Screener.in company page.

Screener is used as a *document index*: it links to the original exchange filings (BSE/NSE PDFs), annual
reports and concall transcripts, and exposes the company's filed results in a standardised table.
Everything we cite on the Pull side is the linked primary PDF; the tables are labelled as Screener's
standardisation of filed results (see docs/data-sources.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from bs4 import BeautifulSoup

from app.services.periods import fiscal_year_of, fy_label, parse_month_year, quarter_of


@dataclass
class ParsedAnnouncement:
    title: str
    url: str
    published_at: datetime  # timezone-aware
    summary: str | None


@dataclass
class ParsedDoc:
    document_type: str  # annual_report / concall_transcript / presentation
    title: str
    url: str
    period_label: str | None
    period_date: date | None


@dataclass
class ParsedMetric:
    table: str
    metric_name: str
    fiscal_period: str
    period_end: date
    period_type: str
    value: float | None
    unit: str


@dataclass
class ParsedCompany:
    name: str
    about: str | None
    bse_code: str | None
    basis: str
    is_financial: bool
    announcements: list[ParsedAnnouncement] = field(default_factory=list)
    documents: list[ParsedDoc] = field(default_factory=list)
    metrics: list[ParsedMetric] = field(default_factory=list)


ROW_MAP = {
    "sales": ("revenue", "crore"),
    "revenue": ("revenue", "crore"),
    "expenses": ("expenses", "crore"),
    "operating profit": ("operating_profit", "crore"),
    "opm %": ("opm_pct", "pct"),
    "financing profit": ("financing_profit", "crore"),
    "financing margin %": ("financing_margin_pct", "pct"),
    "other income": ("other_income", "crore"),
    "interest": ("interest", "crore"),
    "depreciation": ("depreciation", "crore"),
    "profit before tax": ("pbt", "crore"),
    "tax %": ("tax_pct", "pct"),
    "net profit": ("pat", "crore"),
    "eps in rs": ("eps", "rs"),
    "dividend payout %": ("dividend_payout_pct", "pct"),
    "gross npa %": ("gnpa_pct", "pct"),
    "net npa %": ("nnpa_pct", "pct"),
    "equity capital": ("equity_capital", "crore"),
    "reserves": ("reserves", "crore"),
    "borrowings": ("borrowings", "crore"),
    "borrowing": ("borrowings", "crore"),
    "deposits": ("deposits", "crore"),
    "other liabilities": ("other_liabilities", "crore"),
    "total liabilities": ("total_liabilities", "crore"),
    "fixed assets": ("fixed_assets", "crore"),
    "cwip": ("cwip", "crore"),
    "investments": ("investments", "crore"),
    "other assets": ("other_assets", "crore"),
    "total assets": ("total_assets", "crore"),
    "cash from operating activity": ("cfo", "crore"),
    "cash from investing activity": ("cfi", "crore"),
    "cash from financing activity": ("cff", "crore"),
    "net cash flow": ("net_cash_flow", "crore"),
    "free cash flow": ("fcf_screener", "crore"),
    "debtor days": ("debtor_days", "days"),
    "inventory days": ("inventory_days", "days"),
    "days payable": ("days_payable", "days"),
    "cash conversion cycle": ("cash_conversion_cycle", "days"),
    "working capital days": ("working_capital_days", "days"),
    "roce %": ("roce_pct", "pct"),
    "roe %": ("roe_pct", "pct"),
}


def parse_number(text: str) -> float | None:
    t = (text or "").strip().replace(",", "").replace("%", "").replace("₹", "")
    if t in ("", "-", "--", "NA", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _clean_label(text: str) -> str:
    return re.sub(r"\s*\+\s*$", "", text.strip()).strip().lower()


def _period(col: str, table: str) -> tuple[str, date, str] | None:
    end = parse_month_year(col)
    if end is None:
        return None
    if table == "quarters":
        return quarter_of(end).label, end, "quarter"
    if end.month == 3:
        return fy_label(fiscal_year_of(end)), end, "annual"
    # Interim balance-sheet column (e.g. "Sep 2026") or a non-March year end.
    if table == "balance-sheet":
        return f"H1{fy_label(fiscal_year_of(end))}" if end.month == 9 else f"{end:%b%Y}", end, "interim"
    return f"Y{end:%b%Y}", end, "annual"


def _parse_table(soup: BeautifulSoup, section: str) -> list[ParsedMetric]:
    tb = soup.select_one(f"#{section} table")
    if tb is None:
        return []
    headers = [th.get_text(" ", strip=True) for th in tb.select("thead th")]
    out: list[ParsedMetric] = []
    for tr in tb.select("tbody tr"):
        cells = tr.select("td")
        if not cells:
            continue
        label = _clean_label(cells[0].get_text(" ", strip=True))
        if label not in ROW_MAP:
            continue
        name, unit = ROW_MAP[label]
        for col, td in zip(headers[1:], cells[1:]):
            p = _period(col, section)
            if p is None:
                continue
            out.append(ParsedMetric(section, name, p[0], p[1], p[2], parse_number(td.get_text()), unit))
    return out


def parse_company_page(html: str) -> ParsedCompany:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1")
    name = h1.get_text(strip=True) if h1 else "Unknown"
    about_el = soup.select_one(".company-profile .about, .about")
    about = about_el.get_text(" ", strip=True) if about_el else None
    if about:
        about = re.sub(r"^About\s+", "", about)[:1200]

    bse_code = None
    for a in soup.select("a[href*='bseindia.com/stock-share-price']"):
        m = re.search(r"/(\d{6})/", a.get("href", ""))
        if m:
            bse_code = m.group(1)
            break

    q_text = soup.select_one("#quarters")
    basis = "consolidated" if q_text and "Consolidated" in q_text.get_text(" ", strip=True)[:300] else "standalone"
    quarter_labels = [_clean_label(td.get_text(" ", strip=True)) for td in soup.select("#quarters tbody tr td:first-child")]
    is_financial = "financing profit" in quarter_labels

    pc = ParsedCompany(name=name, about=about, bse_code=bse_code, basis=basis, is_financial=is_financial)

    docs = soup.select_one("#documents")
    if docs:
        for block in docs.select(".documents"):
            heading = (block.select_one("h3").get_text(strip=True) if block.select_one("h3") else "").lower()
            if heading.startswith("announcements"):
                for li in block.select("ul.list-links li"):
                    a = li.select_one("a[href]")
                    t = li.select_one("time[datetime]")
                    if not a or not t or not a["href"].startswith("http"):
                        continue
                    sub = a.select_one("div")
                    summary = None
                    if sub:
                        summary = re.sub(r"^\s*\d{1,2} \w{3}( \d{4})?\s*-\s*", "", sub.get_text(" ", strip=True)) or None
                        sub.extract()
                    pc.announcements.append(
                        ParsedAnnouncement(
                            title=re.sub(r"\s+", " ", a.get_text(" ", strip=True)),
                            url=a["href"],
                            published_at=datetime.fromisoformat(t["datetime"]),
                            summary=summary,
                        )
                    )
            elif heading.startswith("annual reports"):
                for a in block.select("ul.list-links a[href]"):
                    title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                    m = re.search(r"(\d{4})", title)
                    if not a["href"].lower().endswith(".pdf"):
                        continue
                    year = int(m.group(1)) if m else None
                    pc.documents.append(
                        ParsedDoc("annual_report", f"Annual Report {year}" if year else title, a["href"],
                                  fy_label(year) if year else None, date(year, 3, 31) if year else None)
                    )
            elif heading.startswith("concalls"):
                for li in block.select("ul.list-links li"):
                    label_el = li.select_one("div")
                    label = label_el.get_text(strip=True) if label_el else None
                    pdate = parse_month_year(label) if label else None
                    for a in li.select("a[href]"):
                        kind = a.get_text(strip=True).lower()
                        if kind == "transcript":
                            pc.documents.append(ParsedDoc("concall_transcript", f"Earnings call transcript — {label}",
                                                          a["href"], label, pdate))
                        elif kind == "ppt":
                            pc.documents.append(ParsedDoc("presentation", f"Investor presentation — {label}",
                                                          a["href"], label, pdate))

    for section in ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios"):
        pc.metrics.extend(_parse_table(soup, section))
    return pc
