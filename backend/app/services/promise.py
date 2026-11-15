"""Promise Tracker assembly: guidance records + revisions + deterministic comparison + both citations."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

import re
from datetime import timedelta

from app.models import Company, Document, DocumentChunk, ManagementGuidance
from app.services.guidance import compare
from app.services.metrics import load_series

METRIC_LABELS = {
    "revenue_growth": "Revenue growth",
    "ebitda_margin": "EBITDA margin",
    "ebit_margin": "EBIT / operating margin",
    "pat_margin": "Net margin",
    "capex": "Capex",
    "loan_growth": "Loan / AUM growth",
    "volume_growth": "Volume growth",
    "margin_unspecified": "Margin (unspecified)",
    "growth_unspecified": "Growth (unspecified)",
}


PLAIN = [
    (r"not mapped to a reported line item", "FINN can't match this to a reported figure: the statement doesn't name a measure FINN tracks, such as revenue growth or margin."),
    (r"Capex is not broken out", "Reported capex isn't in the results data FINN uses, so this can't be checked yet."),
    (r"constant currency", "Guidance is in constant currency; reported rupee figures include currency effects, so they aren't a like-for-like check."),
    (r"single business/segment", "This target is for one business, not the whole company, so company-wide results can't confirm it."),
    (r"not meaningful for a lender", "EBITDA isn't meaningful for a lender, so this can't be scored."),
    (r"Qualitative statement", "No number was given, so there's nothing to score."),
    (r"No reported results yet for", "The target period hasn't been reported yet."),
    (r"Quarter-specific growth targets", "Quarter-level growth targets aren't scored yet."),
    (r"No dated target", "No target date was given, so this is shown for context, not scored."),
    (r"Interim read|Fiscal year not complete|Target period not reached", "Interim read — the target period isn't over yet."),
    (r"EBIT margin derived", "Actual = (operating profit − depreciation) ÷ revenue, calculated by FINN from filed results."),
    (r"OPM %", "Actual = operating profit before other income ÷ revenue (EBITDA proxy), from filed results."),
    (r"basis inferred", "This call frames growth guidance in constant currency."),
]


def plain(note: str) -> str:
    for rx, text in PLAIN:
        if re.search(rx, note or "", re.I):
            return text
    return note


def _section(item: dict) -> str:
    st = item["comparison"]["status"]
    if item["guidance_type"] != "quantitative" or st in ("not_comparable", "unavailable") or item["metric"].endswith("_unspecified"):
        return "other"
    if item["horizon"] == "long_term" or st == "long_term":
        return "aspiration"
    return "commitment"


def _target_text(g: ManagementGuidance) -> str:
    u = "%" if g.unit == "pct" else (" cr" if g.unit == "crore" else "")
    if g.comparator == "range" and g.target_min is not None and g.target_max is not None:
        return f"{g.target_min:g}–{g.target_max:g}{u}"
    if g.target_value is None:
        return "qualitative"
    pre = {">=": "≥ ", "<=": "≤ "}.get(g.comparator or "", "")
    return f"{pre}{'₹' if g.unit == 'crore' else ''}{g.target_value:,.4g}{u}"


def reported_outcome(db: Session, company_id: int, metric: str, period: str, actual: float | None) -> dict | None:
    """Management's own statement of a completed period's outcome (e.g. "the full year margins is at 21%"),
    from management turns of transcripts published within ~4 months after the period ends. Deterministic regex;
    forward-looking sentences (guidance/expect/target) are excluded so next year's guidance is not mistaken for it."""
    if not re.match(r"^FY\d{2}$", period or "") or metric not in ("ebit_margin", "ebitda_margin", "revenue_growth"):
        return None
    end = date(2000 + int(period[2:]), 3, 31)
    docs = db.scalars(select(Document).where(Document.company_id == company_id, Document.document_type == "concall_transcript",
                                             Document.status == "indexed", Document.publication_date > end,
                                             Document.publication_date <= end + timedelta(days=125))).all()
    topic = r"margin" if "margin" in metric else r"revenue|growth"
    yy = period[2:]
    for d in sorted(docs, key=lambda d: d.publication_date):
        chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == d.id, DocumentChunk.speaker_role == "management")
                            .order_by(DocumentChunk.chunk_index)).all()
        for ch in chunks:
            for sent in re.split(r"(?<=[.!?])\s+", ch.content):
                if (re.search(topic, sent, re.I) and re.search(rf"full[- ]year|for the (?:full )?year|FY ?{yy}\b|financial year ['’]?{yy}", sent, re.I)
                        and not re.search(r"guid|expect|target|aspir|will be|outlook", sent, re.I)):
                    m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", sent)
                    if m:
                        v = float(m.group(1))
                        return {"value": v, "statement": sent.strip()[:300], "speaker": ch.speaker,
                                "consistent": None if actual is None else abs(v - actual) <= 0.6,
                                "citation": {"kind": "document", "document_id": d.id, "document_title": d.title, "source_url": d.source_url,
                                             "document_type": d.document_type, "reporting_period": d.reporting_period,
                                             "page_number": ch.page_number, "chunk_id": ch.id, "speaker": ch.speaker,
                                             "speaker_role": "management", "relevant_excerpt": ch.content[:600],
                                             "publication_date": d.publication_date.isoformat()}}
    return None


def promise_tracker(db: Session, company: Company, *, include_qualitative: bool = True) -> dict:
    rows = db.scalars(select(ManagementGuidance).where(ManagementGuidance.company_id == company.id)
                      .order_by(ManagementGuidance.guidance_date, ManagementGuidance.id)).all()
    q, qmeta = load_series(db, company.id, "quarter")
    a, _ = load_series(db, company.id, "annual")
    today = date.today()

    # Revision detection: same metric + same target (period or long-term) restated with a different number.
    latest_by_key: dict[tuple, ManagementGuidance] = {}
    revisions: dict[int, dict] = {}
    for g in rows:
        if g.guidance_type != "quantitative":
            continue
        key = (g.metric, g.target_period or g.horizon)
        prev = latest_by_key.get(key)
        if prev and prev.source_document_id != g.source_document_id:
            changed = (prev.target_value, prev.target_min, prev.target_max) != (g.target_value, g.target_min, g.target_max)
            revisions[g.id] = {"previous_target": _target_text(prev), "previous_date": prev.guidance_date.isoformat()
                               if prev.guidance_date else None, "changed": changed,
                               "previous_guidance_id": prev.id}
        latest_by_key[key] = g

    items = []
    for g in rows:
        if g.guidance_type != "quantitative" and not include_qualitative:
            continue
        comp = compare(g, q, a, is_financial=company.is_financial, today=today)
        outcome = reported_outcome(db, company.id, g.metric, comp.actual_period, comp.actual_value) \
            if comp.status in ("met", "missed") else None
        chunk = db.get(DocumentChunk, g.chunk_id) if g.chunk_id else None
        items.append({
            "id": g.id,
            "metric": g.metric,
            "metric_label": METRIC_LABELS.get(g.metric, g.metric),
            "guidance_type": g.guidance_type,
            "target_text": _target_text(g),
            "target_min": g.target_min, "target_max": g.target_max, "target_value": g.target_value,
            "comparator": g.comparator, "target_period": g.target_period, "horizon": g.horizon, "basis": g.basis,
            "guidance_date": g.guidance_date.isoformat() if g.guidance_date else None,
            "speaker": g.speaker, "statement": g.statement, "conditions": g.conditions, "extractor": g.extractor,
            "revision": revisions.get(g.id),
            "comparison": comp.as_dict(),
            "company_reported": outcome,
            "guidance_citation": {
                "kind": "document", "document_id": g.source_document_id, "document_title": g.document.title,
                "source_url": g.document.source_url, "document_type": g.document.document_type,
                "reporting_period": g.document.reporting_period, "page_number": g.source_page,
                "chunk_id": g.chunk_id, "speaker": g.speaker, "speaker_role": "management",
                "publication_date": g.document.publication_date.isoformat() if g.document.publication_date else None,
                "relevant_excerpt": chunk.content[:600] if chunk else g.statement,
            },
            "actual_citation": {
                "kind": "structured_results", "document_title": f"Filed results ({qmeta.get('basis', 'n/a')})",
                "source_url": qmeta.get("source_url"), "document_type": "standardised_results",
                "reporting_period": comp.actual_period, "relevant_excerpt": comp.method,
            } if comp.actual_value is not None else None,
        })
    # Quantitative first, newest first within each group.
    # Collapse reiterations: same metric, horizon/period, target and basis -> one card (latest quote),
    # with the earlier dates listed. Revisions (changed numbers) stay as separate, linked cards.
    grouped: dict[tuple, dict] = {}
    for it in sorted(items, key=lambda i: i["guidance_date"] or "", reverse=True):
        key = (it["metric"], it["target_period"] or it["horizon"], it["target_text"], it["basis"], it["guidance_type"])
        if key in grouped:
            grouped[key]["reiterated_on"].append(it["guidance_date"])
            continue
        it["reiterated_on"] = []
        grouped[key] = it
    items = list(grouped.values())
    for it in items:
        it["section"] = _section(it)
        it["explanations"] = list(dict.fromkeys(plain(c) for c in it["comparison"]["caveats"] + ([it["conditions"]] if it.get("conditions") else [])))
    # Scored, dated commitments first; then long-term aims; unmapped metrics last; newest first within a group.
    rank = {"met": 0, "missed": 0, "tracking_on": 1, "tracking_below": 1, "pending": 2, "long_term": 3,
            "not_comparable": 4, "unavailable": 5}  # completed (scored) promises first
    items.sort(key=lambda i: (i["guidance_type"] != "quantitative", i["metric"].endswith("_unspecified"),
                              rank.get(i["comparison"]["status"], 5),
                              -(date.fromisoformat(i["guidance_date"]).toordinal() if i["guidance_date"] else 0)))
    quant = [i for i in items if i["guidance_type"] == "quantitative"]
    return {
        "ticker": company.ticker,
        "basis": qmeta.get("basis"),
        "summary": {
            "commitments": sum(i["section"] == "commitment" for i in items),
            "aspirations": sum(i["section"] == "aspiration" for i in items),
            "other": sum(i["section"] == "other" for i in items),
            "quantitative": len(quant),
            "qualitative": len(items) - len(quant),
            "met": sum(i["comparison"]["status"] == "met" for i in quant),
            "missed": sum(i["comparison"]["status"] == "missed" for i in quant),
            "tracking": sum(i["comparison"]["status"].startswith("tracking") for i in quant),
            "revised": sum(bool(i["revision"] and i["revision"]["changed"]) for i in quant),
        },
        "items": items,
    }


def event_links(db: Session, announcement, *, tracker: dict | None = None) -> list[dict]:
    """Promise Ledger items connected to an event by documents, not by topic similarity:
    * an earnings-call filing links to guidance extracted from that same transcript (stated / reiterated / revised);
    * a results filing links to guidance whose comparison uses the quarter it reports."""
    from app.models import Document

    ea = announcement.analysis
    if ea is None or ea.category not in ("earnings_call", "results"):
        return []
    company = announcement.company
    pt = tracker or promise_tracker(db, company)
    out = []
    doc = db.scalars(select(Document).where(Document.company_id == company.id, Document.source_url == announcement.source_url)).first()
    quarter = (ea.facts or {}).get("results_quarter")
    for it in pt["items"]:
        if it["guidance_type"] != "quantitative":
            continue
        cit = it.get("guidance_citation") or {}
        rel = None
        if doc is not None and cit.get("document_id") == doc.id:
            rv = it.get("revision")
            rel = ("revised" if rv and rv.get("changed") else "reiterated" if rv else "stated")
        elif quarter and it["comparison"].get("actual_period") and quarter in it["comparison"]["actual_period"]:
            rel = "result"
        # Link only comparisons that can be checked: a comparable status, a company-level basis, and a measure named in the
        # statement itself (an inferred metric — "anchor our margin target at around 11%" — could be a segment's).
        if rel and (it["comparison"]["status"] in ("unavailable", "not_comparable") or it["basis"] == "segment"
                    or any("inferred" in (c or "") for c in it["comparison"].get("caveats", []) + [it.get("conditions") or ""])
                    or it["comparison"].get("actual_value") is None):
            rel = None
        if rel:
            out.append({"relation": rel, "id": it["id"], "metric_label": it["metric_label"], "target_text": it["target_text"],
                        "target_period": it["target_period"], "horizon": it["horizon"], "basis": it["basis"],
                        "status": it["comparison"]["status"], "actual_value": it["comparison"]["actual_value"],
                        "actual_period": it["comparison"]["actual_period"], "method": it["comparison"]["method"],
                        "revision": it.get("revision"), "statement": it["statement"], "speaker": it.get("speaker"),
                        "guidance_citation": cit, "actual_citation": it.get("actual_citation"),
                        "explanations": it.get("explanations", []), "section": it.get("section"),
                        "company_reported": it.get("company_reported")})
    order = {"commitment": 0, "aspiration": 1, "other": 2}
    return sorted(out, key=lambda x: order.get(x["section"], 3))[:4]
