"""Competing Hypotheses: evidence for and against two or three explanations of one question about one company.

Scope (deliberately narrow so every item is checkable): questions about weaker **cash conversion / working capital**,
answered from annual statements. Structured evidence is computed deterministically (finance.py) with its inputs;
document passages are retrieved with queries that look for support *and* counter-evidence, and — when a model is
available — classified per hypothesis with a verbatim quote that must occur in the passage (else discarded).

Never produced: probabilities, verdicts, or "no mention = disproof". Incompatible requests (quarterly cash flows that
FINN does not hold, lenders whose operating cash flow is mostly loans and deposits) are refused with the reason.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.llm.prompts import HYPOTHESES_PROMPT_VERSION
from app.services import finance

Relation = Literal["supports", "challenges", "does_not_distinguish", "context"]
Stance = Literal["temporary_wc", "persistent", "custom"]

SUGGESTED = [
    {"id": "h1", "stance": "temporary_wc", "origin": "suggested",
     "text": "Temporary: working capital expanded (receivables or inventory) and should unwind."},
    {"id": "h2", "stance": "persistent", "origin": "suggested",
     "text": "Persistent: the business now converts less of its operating profit into cash than it used to."},
]
CASH_Q = re.compile(r"cash conversion|operating cash|cash flow from operations|\bcfo\b|cash generation|working[- ]capital|"
                    r"profit (?:is not|isn't|not) (?:turning|converting) into cash", re.I)
PERIOD_WORDS = re.compile(r"\bquarter\b|\bQ[1-4]\b|\bH[12]\b|first half|nine months|half[- ]year", re.I)
QUARTER_Q = re.compile(r"\bQ[1-4]\b|\bquarter(?:ly)?\b|\bH[12]\b|half[- ]year|\bthis quarter\b", re.I)
WC_DAYS_LIMIT = ("Working-capital, debtor, inventory and payable days are year-end figures from the standardised-data source "
                 "(Screener), not FINN's own calculation; year-end balances can hide swings within the year.")
CONV_LIMIT = ("CFO is after tax and interest paid; operating profit is before both, so the ratio is compared with the company's "
              "own history, not with a benchmark. Consolidated CFO also includes any lending or financial-services subsidiary, whose "
              "loan growth lowers CFO without saying anything about the operating business.")


class Hypothesis(BaseModel):
    id: str = Field(pattern=r"^h[1-3]$")
    text: str = Field(min_length=8, max_length=300)
    stance: Stance = "custom"
    origin: Literal["suggested", "user"] = "user"


def scope_check(company, question: str) -> dict | None:
    """Refuse requests the evidence cannot answer. Returns a refusal or None."""
    if not CASH_Q.search(question or ""):
        return {"status": "unsupported_question",
                "reason": "Competing hypotheses currently covers cash-conversion and working-capital questions, where FINN can "
                          "compute the evidence from filed annual statements. Ask Research for other questions."}
    if company.is_financial:
        return {"status": "incompatible",
                "reason": f"Cash conversion (operating cash flow ÷ operating profit) is not meaningful for {company.company_name}: a "
                          "lender's operating cash flow is dominated by loans disbursed and deposits taken, so it cannot be compared "
                          "with profit."}
    if QUARTER_Q.search(question):
        return {"status": "incompatible",
                "reason": "FINN holds cash-flow statements only for full financial years. Quarterly or half-year cash conversion "
                          "would mix a quarterly profit with an annual cash flow, so FINN will not compute it. Ask about financial "
                          "years instead (e.g. FY26 vs earlier years)."}
    return None


def _fact(company, metric, label, fy, value, unit, basis, meta) -> dict:
    return {"company": company.ticker, "metric": metric, "label": label, "value": value, "unit": unit, "period": fy,
            "period_type": "annual", "basis": basis, "source": {"kind": "standardised_annual", "provider": meta.get("source"),
                                                                 "url": meta.get("source_url"), "fetched_at": meta.get("fetched_at")},
            "display": f"₹{value:,.0f} crore" if unit == "₹ crore" else f"{value:g} {unit}"}


def structured(db: Session, company, hyps: list[Hypothesis]) -> dict:
    """Deterministic evidence from annual statements. Each item: id, kind, statement, explanation, relations, limitations,
    calculation (formula + inputs) or fact."""
    from app.services.metrics import load_series

    s, meta = load_series(db, company.id, "annual")
    basis = meta.get("basis")
    years = sorted((y for y in s.get("cfo", {}) if s["cfo"].get(y) is not None and (s.get("operating_profit") or {}).get(y)),
                   key=lambda y: int(y[2:]))[-5:]
    stance = {h.id: h.stance for h in hyps}
    out: dict = {"items": [], "missing": [], "table": [], "premise": None, "basis": basis, "years": years}
    if len(years) < 4:
        out["missing"].append({"what": "At least four years of operating cash flow and operating profit",
                               "why": f"FINN has {len(years)} year(s) for {company.ticker}; a baseline and a recent period are needed to call "
                                      "conversion weaker, temporary or persistent."})
        return out
    conv = {y: finance.cash_conversion_ebitda_pct(s["cfo"][y], s["operating_profit"][y]) for y in years}
    wc = s.get("working_capital_days", {})
    for y in years:
        out["table"].append({"period": y, "cfo": s["cfo"][y], "operating_profit": s["operating_profit"][y],
                             "conversion_pct": None if conv[y].value is None else round(conv[y].value, 1), "conversion_note": conv[y].note,
                             "working_capital_days": wc.get(y), "debtor_days": s.get("debtor_days", {}).get(y),
                             "inventory_days": s.get("inventory_days", {}).get(y), "days_payable": s.get("days_payable", {}).get(y)})
    base_years, recent = years[:-2][-3:], years[-2:]
    latest, prev = years[-1], years[-2]
    base = finance.median([conv[y].value for y in base_years])
    gap = finance.difference(conv[latest].value, base.value)

    def rel(**by_stance) -> dict:
        return {hid: by_stance.get(st, "context") for hid, st in stance.items()}

    def calc(cid, label, value, unit, period, formula, expression, inputs, statement, explanation, relations, limits) -> dict:
        return {"id": cid, "kind": "calculation", "statement": statement, "explanation": explanation, "relations": relations,
                "limitations": limits, "calculation": {"company": company.ticker, "metric": cid.split(":")[1], "label": label,
                                                       "value": round(value, 1), "unit": unit, "period": period, "basis": basis,
                                                       "formula": formula, "expression": expression, "inputs": inputs,
                                                       "display": f"{value:.1f}{'%' if unit == '%' else ' ' + unit}"}}

    if base.value is None or gap.value is None:
        out["missing"].append({"what": "Cash conversion for the comparison years", "why": conv[latest].note or "operating profit non-positive"})
        return out
    ins = [_fact(company, "cfo", "Cash from operations", y, s["cfo"][y], "₹ crore", basis, meta) for y in base_years + [latest]] + \
          [_fact(company, "operating_profit", "Operating profit (EBITDA)", y, s["operating_profit"][y], "₹ crore", basis, meta) for y in base_years + [latest]]
    weaker = gap.value <= -5
    out["premise"] = {"weaker": weaker, "latest": latest, "latest_pct": round(conv[latest].value, 1), "base_years": base_years,
                      "base_median_pct": round(base.value, 1), "gap_pp": round(gap.value, 1)}
    out["items"].append(calc(
        f"calc:conversion_gap:{latest}", f"Cash conversion {latest} vs {base_years[0]}–{base_years[-1]} median", gap.value, "pp", latest,
        "CFO ÷ operating profit × 100, latest year minus the median of the earlier years",
        f"{conv[latest].value:.1f}% − median({', '.join(f'{conv[y].value:.1f}%' for y in base_years)}) = {gap.value:+.1f} pp",
        ins, f"{latest} cash conversion was {conv[latest].value:.1f}% against a {base.value:.1f}% median for {base_years[0]}–{base_years[-1]} "
             f"({gap.value:+.1f} pp).",
        "This establishes whether conversion is weaker at all; on its own it does not say why." if weaker else
        "Conversion is not weaker than the company's own earlier years by more than 5 pp, so the question's premise is not supported.",
        rel(), [CONV_LIMIT]))
    if not weaker:
        return out
    # Duration: were both recent years below the baseline, or only the latest (or did it already recover)?
    below = [y for y in recent if conv[y].value is not None and conv[y].value <= base.value - 5]
    if below == recent:
        r = rel(temporary_wc="challenges", persistent="supports")
        expl = (f"Both {recent[0]} and {recent[1]} were more than 5 pp below the earlier median. Two consecutive weak years fit a "
                "persistent change better than a one-off; two years is still a short record.")
    elif below == [latest]:
        r = rel(temporary_wc="does_not_distinguish", persistent="does_not_distinguish")
        expl = f"Only {latest} is weak; one year cannot separate a temporary swing from a lasting change."
        out["missing"].append({"what": f"Cash flow after {latest}",
                               "why": "The half-year cash-flow statement filed with the H1 results would show whether conversion recovered; "
                                      "FINN does not extract it yet."})
    else:
        r = rel(temporary_wc="supports", persistent="challenges")
        expl = f"{prev} was weak but {latest} was not as weak — conversion has partly recovered."
    out["items"].append({"id": f"calc:duration:{latest}", "kind": "calculation",
                         "statement": f"Cash conversion by year: {', '.join(f'{y} {conv[y].value:.0f}%' for y in years)}.",
                         "explanation": expl, "relations": r, "limitations": [CONV_LIMIT],
                         "calculation": {"company": company.ticker, "metric": "conversion_series", "label": "Cash conversion by year",
                                         "value": round(conv[latest].value, 1), "unit": "%", "period": f"{years[0]}–{latest}", "basis": basis,
                                         "formula": "CFO ÷ operating profit × 100 for each year",
                                         "expression": "; ".join(f"{y}: {s['cfo'][y]:,.0f} ÷ {s['operating_profit'][y]:,.0f} = {conv[y].value:.1f}%" for y in years),
                                         "inputs": [_fact(company, m, lbl, y, s[m][y], "₹ crore", basis, meta) for y in years
                                                    for m, lbl in (("cfo", "Cash from operations"), ("operating_profit", "Operating profit (EBITDA)"))],
                                         "display": f"{conv[latest].value:.1f}%"}})
    # Driver: did working-capital days rise in the weak year?
    if wc.get(latest) is not None and wc.get(prev) is not None:
        d = finance.difference(wc[latest], wc[prev]).value
        if d >= 5:
            r = rel(temporary_wc="supports", persistent="does_not_distinguish")
            expl = (f"Working capital absorbed cash: days rose by {d:.0f}. This fits the working-capital explanation; it does not say "
                    "whether the build will reverse.")
        elif d <= 0:
            r = rel(temporary_wc="challenges", persistent="does_not_distinguish")
            expl = (f"Working-capital days did not rise ({d:+.0f}), so a working-capital build does not explain the weaker conversion; "
                    "the cause lies elsewhere (e.g. tax, provisions, other operating items).")
        else:
            r = rel()
            expl = f"Working-capital days rose slightly ({d:+.0f}); too small to explain the gap on its own."
        out["items"].append({"id": f"fact:wc_change:{latest}", "kind": "calculation",
                             "statement": f"Working-capital days: {wc[prev]:g} in {prev} → {wc[latest]:g} in {latest} ({d:+.0f} days).",
                             "explanation": expl, "relations": r, "limitations": [WC_DAYS_LIMIT],
                             "calculation": {"company": company.ticker, "metric": "working_capital_days_change", "label": "Change in working-capital days",
                                             "value": d, "unit": "days", "period": f"{prev}→{latest}", "basis": basis, "formula": "days(latest) − days(previous year)",
                                             "expression": f"{wc[latest]:g} − {wc[prev]:g} = {d:+.0f}",
                                             "inputs": [_fact(company, "working_capital_days", "Working-capital days", y, wc[y], "days", basis, meta) for y in (prev, latest)],
                                             "display": f"{d:+.0f} days"}})
        # Duration of the build: rising every year for three years → not a one-off swing.
        seq = [wc.get(y) for y in years[-4:]]
        if all(v is not None for v in seq) and all(b > a for a, b in zip(seq, seq[1:])) and seq[-1] - seq[0] >= 10:
            out["items"].append({"id": f"fact:wc_trend:{latest}", "kind": "calculation",
                                 "statement": f"Working-capital days rose every year: {', '.join(f'{y} {wc[y]:g}' for y in years[-4:])}.",
                                 "explanation": "A build sustained over three years is not a one-off swing; it challenges 'temporary' even though "
                                                "working capital is the mechanism.",
                                 "relations": rel(temporary_wc="challenges", persistent="supports"), "limitations": [WC_DAYS_LIMIT],
                                 "calculation": {"company": company.ticker, "metric": "working_capital_days_trend", "label": "Working-capital days trend",
                                                 "value": seq[-1] - seq[0], "unit": "days", "period": f"{years[-4]}→{latest}", "basis": basis,
                                                 "formula": "days(latest) − days(three years earlier), each year higher than the last",
                                                 "expression": f"{seq[-1]:g} − {seq[0]:g} = {seq[-1] - seq[0]:+.0f}",
                                                 "inputs": [_fact(company, "working_capital_days", "Working-capital days", y, wc[y], "days", basis, meta) for y in years[-4:]],
                                                 "display": f"{seq[-1] - seq[0]:+.0f} days"}})
    # Which component moved (context only).
    comps = []
    for m, lbl in (("debtor_days", "Debtor days"), ("inventory_days", "Inventory days"), ("days_payable", "Payable days")):
        a, b = s.get(m, {}).get(prev), s.get(m, {}).get(latest)
        if a is not None and b is not None:
            comps.append((m, lbl, a, b, finance.difference(b, a).value))
    if comps:
        out["items"].append({"id": f"fact:wc_components:{latest}", "kind": "fact",
                             "statement": "; ".join(f"{lbl} {a:g} → {b:g} ({d:+.0f})" for _, lbl, a, b, d in comps) + f" ({prev}→{latest}).",
                             "explanation": "Shows which part of working capital moved. Higher payable days release cash; higher debtor or "
                                            "inventory days absorb it. It does not tell temporary from persistent.",
                             "relations": rel(temporary_wc="does_not_distinguish", persistent="does_not_distinguish"), "limitations": [WC_DAYS_LIMIT],
                             "facts": [_fact(company, m, lbl, y, v, "days", basis, meta) for m, lbl, a, b, _ in comps for y, v in ((prev, a), (latest, b))]})
    return out


# ------------------------------------------------------------------ document passages

QUERIES = {
    "temporary_wc": ["working capital increase receivables collection", "inventory build up temporary normalise",
                     "working capital release unwind one-off"],
    "persistent": ["longer payment terms structural working capital", "cash conversion lower operating cash flow decline",
                   "elevated receivables extended credit customers"],
}


class _Judgement(BaseModel):
    ref: str
    relations: dict[str, Relation]
    quote: str
    explanation: str = Field(max_length=400)


class _Judgements(BaseModel):
    items: list[_Judgement] = Field(default_factory=list, max_length=10)




def passages(db: Session, company, hyps: list[Hypothesis], *, cache: dict | None = None, allow_llm: bool = True, k: int = 8) -> dict:
    """Retrieve passages for and against each hypothesis; classify with a bounded model call (≤1 call, ≤k passages).
    `cache` maps chunk_id:hypotheses-hash → earlier judgement so revisits don't re-classify unchanged passages."""
    from app.domain.evidence import document_claim
    from app.rag.retriever import search

    qs = list(dict.fromkeys(q for h in hyps for st in ([h.stance] if h.stance != "custom" else ["temporary_wc", "persistent"])
                            for q in QUERIES[st])) + [h.text for h in hyps if h.stance == "custom"]
    hits: dict[int, object] = {}
    for q in qs:
        for hit in search(db, company.id, q, k=4, document_types=["concall_transcript", "annual_report"]):
            hits.setdefault(hit.chunk.id, hit)
    ranked = sorted(hits.values(), key=lambda h: (h.document.publication_date or __import__("datetime").date.min, h.score), reverse=True)
    chosen, seen_txt = [], []
    for h in ranked:   # the same passage in overlapping chunks or duplicate copies is one piece of evidence, not two
        t = re.sub(r"\W+", " ", h.chunk.content.lower()).strip()
        if any(_overlap(t, o) for o in seen_txt):
            continue
        seen_txt.append(t)
        chosen.append(h)
        if len(chosen) >= k:
            break
    hkey = hashlib.sha1(json.dumps([[h.id, h.text] for h in hyps]).encode()).hexdigest()[:10]
    cache = dict(cache or {})
    todo = [h for h in chosen if f"{h.chunk.id}:{hkey}" not in cache]
    stats = {"retrieved": len(hits), "shown": len(chosen), "classified_now": 0, "discarded": 0, "llm": False, "queries": len(qs)}
    if todo and allow_llm:
        from app.llm.client import LLMUnavailable, get_llm, tracked_run
        from app.llm.prompts import HYPOTHESES_SYSTEM

        llm = get_llm()
        if llm is not None:
            system = HYPOTHESES_SYSTEM
            user = json.dumps({"company": company.company_name, "hypotheses": [{"id": h.id, "text": h.text} for h in hyps],
                               "passages": [{"ref": f"P{i}", "document": h.document.title, "period": h.document.reporting_period,
                                             "text": h.chunk.content[:1800]} for i, h in enumerate(todo, 1)]})
            try:
                with tracked_run(db, "hypotheses") as tr:
                    res = llm.structured(tr, "fast", system, user, _Judgements)
                stats["llm"] = True
            except LLMUnavailable:
                res = None
            for j in (res.items if res else []):
                i = int(j.ref[1:]) - 1 if re.fullmatch(r"P\d+", j.ref) else -1
                if not 0 <= i < len(todo) or set(j.relations) != {h.id for h in hyps}:
                    stats["discarded"] += 1
                    continue
                if document_claim(db, todo[i].chunk.id, j.quote) is None:   # quote must be verbatim in the passage
                    stats["discarded"] += 1
                    continue
                if _numeric(j.quote):   # a table row: figures are judged by the computed evidence, never by a model's reading
                    stats["discarded"] += 1
                    stats["numeric_rows"] = stats.get("numeric_rows", 0) + 1
                    continue
                cache[f"{todo[i].chunk.id}:{hkey}"] = {"relations": j.relations, "quote": j.quote, "explanation": j.explanation,
                                                        "prompt_version": HYPOTHESES_PROMPT_VERSION}
                stats["classified_now"] += 1
    items = []
    for h in chosen:
        j = cache.get(f"{h.chunk.id}:{hkey}")
        claim = document_claim(db, h.chunk.id, j["quote"] if j else None, max_len=320)
        if claim is None:
            continue
        items.append({"id": f"doc:{h.chunk.id}", "kind": "document", "document": claim.model_dump(),
                      "statement": claim.passage, "relations": j["relations"] if j else {x.id: "context" for x in hyps},
                      "explanation": j["explanation"] if j else "Not classified: no AI judgement is available for this passage, so it is shown as context.",
                      "classified_by": "ai" if j else None,
                      "period_mismatch": bool(PERIOD_WORDS.search(claim.passage)),
                      "limitations": (["Refers to a quarter or half-year; the computed figures are full financial years, so the two are "
                                       "not directly comparable."] if PERIOD_WORDS.search(claim.passage) else []) +
                                     (["AI classification of a passage — read the quote; it is labelled, not verified as a conclusion."] if j else [])})
    # drop items whose quoted passage is contained in another item's passage (keep the fuller quote)
    norm = {i["id"]: re.sub(r"\W+", " ", i["statement"].lower()).strip() for i in items}
    items = [i for i in items if not any(j is not i and norm[i["id"]] in norm[j["id"]] and (len(norm[i["id"]]) < len(norm[j["id"]]) or j["id"] < i["id"])
                                         for j in items)]
    return {"items": items, "stats": stats, "cache": cache}


def _numeric(text: str) -> bool:
    """Mostly figures (a statement row like '(Increase)/decrease in trade receivables 20,888.4 (27,141.0)')."""
    digits = sum(ch.isdigit() for ch in text)
    words = re.findall(r"[A-Za-z]{3,}", text)
    return digits >= 6 and (digits / max(1, len(text.replace(" ", ""))) > 0.2 or len(words) < 8)


def _overlap(a: str, b: str) -> bool:
    """True when two chunk texts share most of their sentences (overlapping windows or duplicate documents)."""
    sa = {x.strip() for x in re.split(r"(?<=[a-z0-9]) (?=[a-z])", a) if len(x) > 40} or {a}
    wa, wb = set(a.split()), set(b.split())
    return bool(wa) and len(wa & wb) / max(1, min(len(wa), len(wb))) > 0.8 or any(x in b for x in sa if len(x) > 120)


# ------------------------------------------------------------------ assemble

def summarise(hyps: list[Hypothesis], items: list[dict], premise: dict | None) -> dict:
    per = {h.id: {"supports": [], "challenges": [], "does_not_distinguish": []} for h in hyps}
    for it in items:
        for hid, r in (it.get("relations") or {}).items():
            if hid in per and r in per[hid]:
                per[hid][r].append(it["id"])
    if premise is not None and not premise["weaker"]:
        state, text = "premise_not_supported", (f"{premise['latest']} cash conversion ({premise['latest_pct']}%) is not more than 5 pp below its "
                                                f"{premise['base_years'][0]}–{premise['base_years'][-1]} median ({premise['base_median_pct']}%), so there is "
                                                "no weaker conversion to explain.")
    elif premise is None:
        state, text = "insufficient", "FINN does not have enough annual cash-flow history to test these hypotheses."
    else:
        any_support = any(p["supports"] for p in per.values())
        conflicted = any(p["supports"] and p["challenges"] for p in per.values()) or sum(bool(p["supports"]) for p in per.values()) > 1
        if not any_support:
            state, text = "insufficient", ("Nothing in the evidence positively supports any hypothesis; " +
                                           ("some evidence argues against one, but " if any(p["challenges"] for p in per.values()) else "") +
                                           "the question cannot be settled with what FINN holds.")
        elif conflicted:
            state, text = "conflicting", "The evidence points both ways. FINN lists it rather than weighing it into a verdict."
        else:
            state, text = "one_sided", ("The evidence found so far supports one hypothesis and none of it supports the others. "
                                        "That is a description of the evidence, not a conclusion or a probability.")
    return {"state": state, "text": text, "by_hypothesis": per}


def run(db: Session, company, question: str, hyps: list[Hypothesis], *, cache: dict | None = None, allow_llm: bool = True) -> dict:
    refusal = scope_check(company, question)
    if refusal:
        return {"status": refusal["status"], "reason": refusal["reason"], "items": [], "missing": [], "table": [],
                "summary": {"state": refusal["status"], "text": refusal["reason"], "by_hypothesis": {}}, "next_questions": [], "cache": cache or {}}
    st = structured(db, company, hyps)
    docs = passages(db, company, hyps, cache=cache, allow_llm=allow_llm) if st["premise"] and st["premise"]["weaker"] else \
        {"items": [], "stats": {"skipped": "premise not supported"}, "cache": cache or {}}
    items = st["items"] + docs["items"]
    missing = list(st["missing"])
    if st["premise"] and st["premise"]["weaker"] and not any(any(r in ("supports", "challenges") for r in i["relations"].values())
                                                            for i in docs["items"]):
        missing.append({"what": "Management's explanation", "why": "No passage FINN holds explains the cash conversion in a way that bears on "
                                                                     "these hypotheses. That is a gap, not evidence against either."})
    summary = summarise(hyps, items, st["premise"])
    nq = []
    if st["premise"] and st["premise"]["weaker"]:
        nq.append(f"Does the half-year cash-flow statement after {st['premise']['latest']} show working capital releasing?")
        if any(i["id"].startswith("fact:wc_components") for i in items):
            nq.append("What does management say drove the change in debtor and inventory days?")
        if summary["state"] in ("conflicting", "insufficient"):
            nq.append("Is the working-capital build tied to specific contracts or customers that management expects to reverse?")
    fp = hashlib.sha1(json.dumps([[i["id"], i.get("relations"), i.get("statement")] for i in items] + [m["what"] for m in missing],
                                 sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"status": "ok", "premise": st["premise"], "basis": st["basis"], "table": st["table"], "items": items, "missing": missing,
            "summary": summary, "next_questions": nq, "retrieval": docs["stats"], "fingerprint": fp, "cache": docs["cache"]}


# ------------------------------------------------------------------ persistence (versions, revisits, Handover)

def _public(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "cache"}


def diff(old: dict | None, new: dict) -> dict:
    """What changed between two evidence versions: items added/removed and relations that changed."""
    if not old:
        return {"added": [], "removed": [], "changed": []}
    o = {i["id"]: i for i in old.get("items", [])}
    n = {i["id"]: i for i in new.get("items", [])}
    return {"added": [n[k]["statement"][:160] for k in n.keys() - o.keys()],
            "removed": [o[k]["statement"][:160] for k in o.keys() - n.keys()],
            "changed": [n[k]["statement"][:160] for k in n.keys() & o.keys() if n[k].get("relations") != o[k].get("relations")],
            "state": None if old.get("summary", {}).get("state") == new["summary"]["state"] else
                     {"from": old.get("summary", {}).get("state"), "to": new["summary"]["state"]}}


def save_version(db: Session, inv, result: dict) -> bool:
    """Store a new evidence version only when the evidence actually changed (fingerprint). Returns True if it did."""
    from app.core.db import utcnow
    from app.models import InvestigationVersion

    inv.judgement_cache = result.get("cache") or inv.judgement_cache or {}
    inv.last_checked_at = utcnow()
    last = db.query(InvestigationVersion).filter_by(investigation_id=inv.id).order_by(InvestigationVersion.version.desc()).first()
    if last is not None and last.fingerprint == result.get("fingerprint"):
        db.commit()
        return False
    v = (last.version + 1) if last else 1
    db.add(InvestigationVersion(investigation_id=inv.id, version=v, fingerprint=result.get("fingerprint") or "none", result=_public(result)))
    inv.current_version, inv.status, inv.updated_at = v, result["summary"]["state"], utcnow()
    if last is None:
        inv.reviewed_version = v
    db.commit()
    return True


def refresh_all(db: Session) -> dict:
    """Scheduled revisit: recompute every investigation with the evidence FINN now holds. No model calls here — new passages
    stay unclassified until the investor re-runs the comparison — so a refresh has bounded cost."""
    from app.models import Investigation

    out = {"checked": 0, "changed": 0}
    for inv in db.query(Investigation).all():
        hyps = [Hypothesis(**h) for h in inv.hypotheses]
        res = run(db, inv.company, inv.question, hyps, cache=inv.judgement_cache, allow_llm=False)
        out["checked"] += 1
        out["changed"] += int(save_version(db, inv, res))
    return out
