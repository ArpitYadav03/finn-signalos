"""Structured-metric queries: numerical questions are answered from here, never from document text."""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, FinancialMetric
from app.services import finance
from app.services.periods import FiscalQuarter, quarter_of

Series = dict[str, float | None]


def load_series(db: Session, company_id: int, period_type: str) -> tuple[dict[str, Series], dict]:
    rows = db.scalars(
        select(FinancialMetric).where(FinancialMetric.company_id == company_id, FinancialMetric.period_type == period_type)
    ).all()
    out: dict[str, Series] = {}
    meta: dict = {}
    for r in rows:
        out.setdefault(r.metric_name, {})[r.fiscal_period] = r.metric_value
        meta = {"basis": r.statement_basis, "source": r.source, "source_url": r.source_url,
                "fetched_at": r.fetched_at.isoformat() + "Z" if r.fetched_at else None}
    # Derived lines (deterministic): net interest income for lenders = interest earned - interest expended.
    # Screener's "financing profit" also nets operating costs/provisions and, on consolidated bank accounts,
    # is distorted by insurance subsidiaries — so NII is the honest headline for banks.
    if "financing_profit" in out and "revenue" in out and "interest" in out:
        out["nii"] = {p: (v - out["interest"][p]) if v is not None and out["interest"].get(p) is not None else None
                      for p, v in out["revenue"].items()}
        out["nii_margin_pct"] = {p: finance.margin_pct(out["nii"].get(p), out["revenue"].get(p)).value for p in out["revenue"]}
    if "pat" in out and "revenue" in out:
        out["pat_margin_pct"] = {p: finance.margin_pct(out["pat"].get(p), out["revenue"].get(p)).value for p in out["revenue"]}
    return out, meta


def _qsort(p: str) -> tuple[int, int]:
    return int(p[4:]), int(p[1])


def headline_lines(company: Company) -> list[tuple[str, str]]:
    """The four headline lines per the brief. Lenders have no EBITDA: we show net interest income instead."""
    if company.is_financial:
        return [("revenue", "Interest earned"), ("nii", "Net interest income*"), ("pbt", "PBT"), ("pat", "PAT")]
    return [("revenue", "Revenue"), ("operating_profit", "EBITDA*"), ("pbt", "PBT"), ("pat", "PAT")]


def results_snapshot(db: Session, company: Company, quarter: str | None = None) -> dict | None:
    """Headline numbers for a quarter with deterministic QoQ and YoY."""
    q, meta = load_series(db, company.id, "quarter")
    rev = q.get("revenue", {})
    periods = sorted((p for p, v in rev.items() if v is not None), key=_qsort)
    if not periods:
        return None
    cur = quarter or periods[-1]
    if cur not in rev:
        return None
    fq = FiscalQuarter(2000 + int(cur[4:]), int(cur[1]))
    prev, ly = fq.shift(-1).label, fq.shift(-4).label
    lines = []
    for key, label in headline_lines(company):
        s = q.get(key, {})
        v = s.get(cur)
        lines.append({
            "metric": key, "label": label, "value": v, "unit": "₹ cr",
            "qoq": finance.qoq_pct(v, s.get(prev)).as_dict(),
            "yoy": finance.yoy_pct(v, s.get(ly)).as_dict(),
            # Calculation inputs, so every growth figure can be re-derived by the reader.
            "inputs": {"current": v, "previous_quarter": s.get(prev), "year_ago_quarter": s.get(ly)},
            "derived": key == "nii",
        })
    margin_key = "pat_margin_pct" if company.is_financial else "opm_pct"
    m = q.get(margin_key, {})
    return {
        "quarter": cur, "previous_quarter": prev, "year_ago_quarter": ly, "lines": lines,
        "margin": {"label": "PAT ÷ interest earned" if company.is_financial else "EBITDA margin*", "value": m.get(cur),
                   "previous": m.get(prev), "year_ago": m.get(ly),
                   "change_yoy_pp": finance.pp_diff(m.get(cur), m.get(ly)).as_dict()},
        "basis": meta.get("basis"), "source": meta.get("source"), "source_url": meta.get("source_url"),
        "primary_filing": primary_results_filing(db, company.id, fq, [l["value"] for l in lines if l["value"] is not None and not l["derived"]],
                                                 meta.get("basis")),
        "formula": {"qoq": "(this quarter − previous quarter) ÷ |previous quarter| × 100",
                    "yoy": "(this quarter − same quarter last year) ÷ |same quarter last year| × 100"},
        "notes": ["*EBITDA shown as operating profit before other income, as standardised from the filed results."]
        if not company.is_financial else ["*Lender: EBITDA is not meaningful. Net interest income = interest earned − interest expended, computed by FINN from the filed results."],
    }


def _number_variants(v: float) -> set[str]:
    """How a ₹-crore figure may be printed in a results table: 4229, 4,229, 4229.07 (integer part), rounded either way."""
    out = set()
    for n in {int(v), int(round(v))}:
        if abs(n) < 100:
            continue
        s = str(abs(n))
        head = s[:-3]   # Indian grouping 1,23,456 vs western 123,456
        indian = (",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1]) + "," + s[-3:]) if head else s
        out |= {s, indian, f"{abs(n):,}"}
    return out


def _figures_chunk(chunks: list, values: list[float], basis: str | None):
    """The passage whose text contains the most reported figures, preferring the statement for the same basis."""
    variants = [_number_variants(v) for v in values]
    best, best_score = None, 0
    for c in chunks:
        text = c.content
        hits = sum(1 for vs in variants if any(re.search(rf"(?<![\d,]){re.escape(x)}(?![\d,])", text) for x in vs))
        if not hits:
            continue
        score = hits * 10 + (5 if basis and basis.lower() in text.lower() else 0)
        if score > best_score:
            best, best_score = c, score
    return best


def primary_results_filing(db: Session, company_id: int, fq: FiscalQuarter, values: list[float] | None = None,
                           basis: str | None = None) -> dict | None:
    """The company's own results filing for a quarter (BSE PDF), filed within ~75 days after quarter end, pointing at the
    page whose text contains the reported figures (falls back to the first page)."""
    from app.models import Announcement, Document, DocumentChunk

    end = fq.end_date
    rows = db.scalars(select(Announcement).where(Announcement.company_id == company_id, Announcement.category == "results")).all()
    rows = [a for a in rows if end < a.published_at.date() <= end + timedelta(days=75)]
    if not rows:
        return None
    rows.sort(key=lambda a: (0 if "financial result" in a.title.lower() else 1, a.published_at))
    a = rows[0]
    doc = db.scalars(select(Document).where(Document.company_id == company_id, Document.source_url == a.source_url,
                                            Document.status == "indexed")).first()
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.chunk_index)).all() if doc else []
    chunk = (_figures_chunk(chunks, values or [], basis) if values else None) or (chunks[0] if chunks else None)
    return {"title": a.title, "source_url": a.source_url, "published_at": a.published_at.isoformat() + "Z",
            "document_id": doc.id if doc else None, "chunk_id": chunk.id if chunk else None,
            "page_number": chunk.page_number if chunk else None, "pages": doc.page_count if doc else None}


def quarter_for_filing(published: date) -> str:
    """Results filed in Oct-Nov relate to the quarter that ended in September, etc."""
    return quarter_of(published).shift(-1).label


def annual_overview(db: Session, company: Company) -> dict:
    a, meta = load_series(db, company.id, "annual")
    years = sorted(p for p in a.get("revenue", {}) if p.startswith("FY") and a["revenue"][p] is not None)
    rows = []
    for y in years:
        rev, pat = a["revenue"].get(y), a.get("pat", {}).get(y)
        op = a.get("operating_profit", {}).get(y)
        cfo = a.get("cfo", {}).get(y)
        eq = None
        if a.get("equity_capital", {}).get(y) is not None and a.get("reserves", {}).get(y) is not None:
            eq = a["equity_capital"][y] + a["reserves"][y]
        rows.append({
            "period": y, "revenue": rev, "operating_profit": op, "pat": pat,
            "equity_capital": a.get("equity_capital", {}).get(y), "reserves": a.get("reserves", {}).get(y),
            "opm_pct": a.get("opm_pct", {}).get(y),
            "financing_margin_pct": a.get("financing_margin_pct", {}).get(y),
            "nii": a.get("nii", {}).get(y), "pat_margin_pct": a.get("pat_margin_pct", {}).get(y),
            "cfo": cfo, "fcf": a.get("fcf_screener", {}).get(y),
            "borrowings": a.get("borrowings", {}).get(y), "equity": eq,
            "debt_to_equity": finance.debt_to_equity(a.get("borrowings", {}).get(y), eq).as_dict()["value"]
            if not company.is_financial else None,
            "cash_conversion": finance.cash_conversion(cfo, pat).as_dict()["value"],
            "roce_pct": a.get("roce_pct", {}).get(y), "roe_pct": a.get("roe_pct", {}).get(y),
            "working_capital_days": a.get("working_capital_days", {}).get(y),
            "cash_conversion_cycle": a.get("cash_conversion_cycle", {}).get(y),
        })
    cagr = {}
    if len(years) >= 6:
        for k in ("revenue", "pat"):
            cagr[f"{k}_5y_cagr_pct"] = finance.cagr_pct(a[k].get(years[-6]), a[k].get(years[-1]), 5).as_dict()
    if len(years) >= 4:
        for k in ("revenue", "pat"):
            cagr[f"{k}_3y_cagr_pct"] = finance.cagr_pct(a[k].get(years[-4]), a[k].get(years[-1]), 3).as_dict()
    return {"years": rows, "cagr": cagr, **meta}


# ------------------------------------------------------------------ primary-document location of annual figures

ANNUAL_DOC_PATTERNS = {
    "cfo": r"net cash (?:flow )?(?:generated |inflow )?(?:from|by|used in) operating activities|net cash from operating activities",
    "pat": r"profit for the (?:year|period)|profit after tax|net profit for the year",
    "revenue": r"revenue from operations|total income from operations",
    "borrowings": r"\bborrowings\b",
}
_annual_ref_cache: dict[tuple, dict | None] = {}


def primary_annual_reference(db: Session, company_id: int, fiscal_year: str, metric: str, value: float | None) -> dict | None:
    """The annual-report page that prints this figure (e.g. the cash-flow statement line 'Net cash generated from
    operating activities 7,088.18'). Looks in the annual report for that fiscal year, then the next year's report
    (prior-year column). Requires the metric's line label and the value (to rounding) within the same passage."""
    from app.models import Document, DocumentChunk

    if value is None or metric not in ANNUAL_DOC_PATTERNS:
        return None
    key = (company_id, fiscal_year, metric, round(value), 2)
    if key in _annual_ref_cache:
        return _annual_ref_cache[key]
    fy = int(fiscal_year[2:])
    docs = db.scalars(select(Document).where(Document.company_id == company_id, Document.document_type == "annual_report",
                                             Document.status == "indexed")).all()
    ordered = [d for d in docs if d.reporting_period == f"FY{fy:02d}"] + [d for d in docs if d.reporting_period == f"FY{fy + 1:02d}"]
    label = re.compile(ANNUAL_DOC_PATTERNS[metric], re.I)
    n = abs(value)
    variants = _number_variants(n) | _number_variants(n - 1 if n == int(n) else n)   # rounded values may truncate down
    out = None
    for d in ordered:
        for ch in db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == d.id).order_by(DocumentChunk.chunk_index)).all():
            text = re.sub(r"\s+", " ", ch.content)
            for m in label.finditer(text):
                seg = text[m.end(): m.end() + 140]
                hm = next((mm for v in variants for mm in [re.search(rf"(?<![\d,]){re.escape(v)}(?:\.\d+)?(?![\d,])", seg)] if mm), None)
                hit = hm.group(0) if hm else None
                if hit:
                    out = {"document_id": d.id, "chunk_id": ch.id, "page_number": ch.page_number, "title": d.title,
                           "reporting_period": d.reporting_period, "source_url": d.source_url,
                           "excerpt": (m.group(0) + " … " + seg[max(0, hm.start() - 25): hm.end()].strip()) if hm.start() > 40
                           else text[m.start(): m.end() + hm.end() + 10].strip(),
                           "printed_value": hit,
                           "column": "current year" if d.reporting_period == f"FY{fy:02d}" else "prior-year column"}
                    break
            if out:
                break
        if out:
            break
    _annual_ref_cache[key] = out
    return out
