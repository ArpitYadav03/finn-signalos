"""Management Promise Tracker: extract guidance from transcripts and compare it with reported numbers.

Extraction (rules-v1) is deliberately conservative:
  * only management speakers (never analysts or the moderator)
  * only forward-looking sentences, never questions
  * a number must be present for "quantitative"; otherwise it is stored as qualitative and never scored
An optional LLM extractor (agents/guidance_llm.py) can add items, but every LLM item must quote text that
literally exists in the source chunk, or it is discarded.

Comparison is pure Python and refuses to compare when period or basis do not line up.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from app.services import finance
from app.services.periods import FiscalQuarter, fiscal_year_of, fy_label, parse_fy_token, quarter_of

FORWARD = re.compile(
    r"\b(expect|guid|target|aspir|aim(ing)?\b|outlook|anticipat|we will|we'll|should (be|see|reach)|going forward|"
    r"plan(ning)? to|commit|confident|look(ing)? to|trajectory|exit(ing)?\s+(fy|the year)|by (the )?(end of )?fy|"
    r"next (year|fiscal|quarter)|this (year|fiscal)|medium[- ]term|long[- ]term|intend|envisage|on track)",
    re.I,
)
METRICS: list[tuple[str, re.Pattern]] = [
    ("ebitda_margin", re.compile(r"ebitda margin", re.I)),
    ("ebit_margin", re.compile(r"\bebit margin|operating margin|\bebit\b", re.I)),
    ("pat_margin", re.compile(r"(pat|net profit|net) margin", re.I)),
    ("margin_unspecified", re.compile(r"\bmargins?\b", re.I)),
    ("capex", re.compile(r"\bcapex\b|capital expenditure", re.I)),
    ("revenue_growth", re.compile(r"\b(revenue|top[- ]?line|sales)\b.*\bgrow|\bgrow\w*\b.*\b(revenue|top[- ]?line|sales)\b|"
                                  r"(revenue|top[- ]?line|sales) growth", re.I)),
    ("loan_growth", re.compile(r"(loan|credit|advances?|aum|book) growth", re.I)),
    ("volume_growth", re.compile(r"volume growth", re.I)),
    ("growth_unspecified", re.compile(r"\bgrowth\b|\bgrow\b", re.I)),
]
PCT_RANGE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%?\s*(?:to|-|–|and)\s*(\d{1,2}(?:\.\d+)?)\s*%")
PCT_ONE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%\s*(plus|\+)?")
INR_AMOUNT = re.compile(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|million|billion|bn)", re.I)
WORD_RANGES = {
    r"high single[- ]digit": (7.0, 9.0),
    r"mid single[- ]digit": (4.0, 6.0),
    r"low single[- ]digit": (1.0, 3.0),
    r"double[- ]digit": (10.0, None),
    r"mid[- ]teens": (14.0, 16.0),
    r"high[- ]teens": (17.0, 19.0),
    r"low[- ]teens": (11.0, 13.0),
}


@dataclass
class GuidanceCandidate:
    metric: str
    guidance_type: str  # quantitative / qualitative
    statement: str
    target_value: float | None = None
    target_min: float | None = None
    target_max: float | None = None
    comparator: str | None = None
    unit: str | None = None
    basis: str = "reported"
    horizon: str = "period"
    target_period: str | None = None
    conditions: str | None = None


# Percentages that are not company guidance: macro data, stakes, market shares, JV splits.
NOT_GUIDANCE = re.compile(r"\b(holder|stake|shareholding|market share|of the (total )?market|joint venture|\bjv\b|imf|gdp|"
                          r"inflation|repo rate|global growth|industry (is|will) grow|economy|payout|capital allocation|"
                          r"return to shareholders|free cash flow|attrition|utili[sz]ation|onsite mix|headcount|conversion|"
                          r"r&d|research and development|tax rate|effective tax)\b|"
                          # A share of revenue is a mix statement, not a growth or margin target:
                          # "AI first revenue now accounts for 10.5% of our revenues" (Infosys, Oct 2026).
                          r"\baccounts? for (?:about |around |over |nearly )?\d+(?:\.\d+)?\s*%|"
                          # One deal's or segment's contribution to growth is not the company's growth target:
                          # "This deal is expected to contribute over 1% revenue growth in the coming financial year."
                          r"\bcontribut\w*\s+(?:to\s+)?(?:about |around |over |nearly |more than )?\d+(?:\.\d+)?\s*%|"
                          r"\bfrom (?:that|this|the|one|a) (?:one |single |mega |large |big )*deals?\b|"
                          r"\b\d+(?:\.\d+)?\s*% of (?:our |the |total |overall )*(?:revenues?|sales|turnover|topline)\b", re.I)
# Plausible ranges for a company-level target, used to reject numbers attached to the wrong metric
# (e.g. a "1.5% to 3%" revenue range picked up as a margin target because the analyst asked about margins).
PLAUSIBLE = {"ebitda_margin": (3, 70), "ebit_margin": (3, 60), "pat_margin": (1, 50), "revenue_growth": (-30, 80),
             "loan_growth": (-20, 80), "volume_growth": (-30, 80)}
QUESTION_FORM = re.compile(r"^(and )?(so )?(in context of that, )?(do|does|would|could|can|is|are|should|will) (we|you|it|there|the)\b|"
                           r"\b(do|would|can) you (expect|see|think)\b", re.I)
HISTORICAL = re.compile(r"\b(maintained|stood at|was at|were at|came in at|delivered|reported|ended the (?:year|quarter)|"
                        r"for (?:the )?(?:h1|first half|quarter|year) (?:was|stood)|we did|have absorbed|grew|"
                        r"(?:first half|second half|h1|h2|this quarter|last quarter|q[1-4]|ytd|year[- ]to[- ]date) (?:is|was|has been)|"
                        r"have been \w+ing|has been \w+ing|(?:on a )?(?:nine|six|three)[- ]months? basis|nine[- ]months? performance|"
                        r"we ended the (?:year|quarter)|will (?:now )?(?:discuss|take you|walk you|hand|cover|present|share|talk))\b", re.I)
# A history sentence counts as guidance only if it states the guidance itself ("we expect…", "guidance is/remains…"),
# not merely because it mentions a guided range ("H1 margin stood at 21%, the midpoint of our guided range").
STATES_GUIDANCE = re.compile(r"\b(expect|will(?!\s+(?:now\s+)?(?:discuss|take|walk|hand|cover|present|share|talk))|should|aim(?:ing)? to|plan(?:ning)? to|going forward|guidance (?:is|remains|for|of \d)|"
                             r"target (?:is|for|of))\b", re.I)
PAST_TENSE = re.compile(r"\b(grew|declined|was|were|reported|delivered|stood at|came in|increased|decreased)\b", re.I)
EXPLICIT_FORWARD = re.compile(r"\b(expect|guid|target|aspir|outlook|will|should|plan|commit|aim|exit)", re.I)


def _sentences(text: str) -> list[str]:
    # Bullets from PDFs arrive as private-use glyphs; treat them as sentence breaks.
    text = re.sub(r"[\uf000-\uf8ff•▪●]", ". ", text)
    return [s.strip(" .") + "." for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", text) if len(s.split()) >= 5]


def dominant_margin_metric(full_text: str) -> str | None:
    """Which margin a company habitually talks about (used when a sentence just says 'margin')."""
    t = full_text.lower()
    ebitda = len(re.findall(r"ebitda margin", t))
    ebit = len(re.findall(r"\bebit margin|operating margin", t))
    if max(ebitda, ebit) < 2:
        return None
    return "ebitda_margin" if ebitda > ebit else "ebit_margin"


def _specific_margin(text: str) -> str | None:
    if re.search(r"ebitda margin", text, re.I):
        return "ebitda_margin"
    if re.search(r"\bebit margin|operating margin", text, re.I):
        return "ebit_margin"
    return None


def _metric_of(sentence: str) -> str | None:
    for name, rx in METRICS:
        if rx.search(sentence):
            return name
    return None


def _target_period(sentence: str, ref: date) -> tuple[str | None, str]:
    s = sentence.lower()
    if re.search(r"long[- ]term|medium[- ]term|aspiration|over (the )?(next )?(few|3|three|5|five) years|sustainabl", s):
        horizon = "long_term"
    else:
        horizon = "period"
    m = re.search(r"exit(?:ing)?\s+(?:the year\s+)?(fy\s?'?\d{2,4})", s)
    if m:
        fy = parse_fy_token(m.group(1))
        return (FiscalQuarter(fy, 4).label if fy else None), horizon
    m = re.search(r"(q[1-4])\s*(fy\s?'?\d{2,4})", s)
    if m:
        fy = parse_fy_token(m.group(2))
        return (f"{m.group(1).upper()}FY{fy % 100:02d}" if fy else None), horizon
    s = re.sub(r"\b(?:financial|fiscal)\s+year\s*['’]?\s*(?=\d)", "fy", s).replace("’", "'")
    m = re.search(r"fy\s?'?\d{2,4}|20\d{2}\s*[-–]\s*\d{2}", s)
    if m:
        fy = parse_fy_token(m.group(0))
        return (fy_label(fy) if fy else None), horizon
    if re.search(r"exit(ing)? (this|the) year|by (the )?(end of )?(this|the) year|by q4|fourth quarter", s):
        return FiscalQuarter(fiscal_year_of(ref), 4).label, horizon
    if horizon == "period" and re.search(r"for the (full |fiscal |financial |whole )?year|full[- ]year|for this (financial |fiscal )?year|"
                                      r"(for )?(the )?rest of the year|remainder of the year", s):
        return fy_label(fiscal_year_of(ref)), horizon
    fy = parse_fy_token(s, reference=ref)
    if fy:
        return fy_label(fy), horizon
    if "next quarter" in s:
        return quarter_of(ref).shift(1).label, horizon
    return None, horizon


def extract_guidance(text: str, ref_date: date, *, question_context: str | None = None,
                     default_margin: str | None = None) -> list[GuidanceCandidate]:
    """question_context: the analyst question this management answer responds to (metric inference only)."""
    out: list[GuidanceCandidate] = []
    raw = _sentences(text)
    # One sentence can carry two commitments ("12-14% revenue growth, and EBITDA margin of at least 18%"):
    # split into clauses when more than one clause names a metric; clauses inherit the sentence's period/cue.
    sents: list[str] = []
    parents: list[str] = []
    for sent in raw:
        clauses = [c.strip() for c in re.split(r",\s*(?:and|while|whereas|with)\s+|;\s*", sent) if len(c.split()) >= 3]
        if len(clauses) > 1 and sum(1 for c in clauses if _metric_of(c) and PCT_ONE.search(c)) > 1:
            sents.extend(clauses)
            parents.extend([sent] * len(clauses))
        else:
            sents.append(sent)
            parents.append(sent)
    for i, sent in enumerate(sents):
        parent = parents[i]
        if parent.endswith("?") or not FORWARD.search(parent):
            continue
        if PAST_TENSE.search(sent) and not EXPLICIT_FORWARD.search(parent):
            continue
        if NOT_GUIDANCE.search(sent) or QUESTION_FORM.search(parent.strip()):
            continue
        # Historical performance ("margins stood at 21%", "we have maintained margins at 21%") is not a promise,
        # unless the same sentence explicitly frames a target.
        hist, states = HISTORICAL.search(sent), STATES_GUIDANCE.search(sent)
        if hist and not states:
            continue
        if hist and states and hist.start() < states.start():
            nums = [m.start() for m in PCT_ONE.finditer(sent)]
            if nums and all(n < states.start() for n in nums):
                continue   # "We ended the year with 21.1%, with an endeavor to increase margin going forward": the figure is history
        has_number = bool(PCT_ONE.search(sent) or INR_AMOUNT.search(sent))
        metric = _metric_of(sent)
        inferred = None
        if metric is None and has_number:
            # "continues to be 26% to 28%" — the metric lives in the preceding sentence or the analyst question.
            for ctx, label in ((sents[i - 1] if i else "", "previous sentence"), (question_context or "", "analyst question")):
                metric = _metric_of(ctx)
                if metric:
                    inferred = f"metric inferred from {label}"
                    if metric == "margin_unspecified":
                        metric = _specific_margin(ctx) or metric
                    break
        if metric is None:
            continue
        if metric == "margin_unspecified":
            specific = _specific_margin(text) or default_margin
            if specific:
                metric = specific
                inferred = (inferred + "; " if inferred else "") + "margin type inferred from the company's usage in this call"
        period, horizon = _target_period(sent, ref_date)
        if period is None and parent != sent:
            period, horizon = _target_period(parent, ref_date)
        basis = "constant_currency" if re.search(r"constant currency|\bcc\b|in dollar|usd|\$", parent, re.I) else "reported"
        # Guidance for one business line can't be checked against company-wide numbers.
        if re.search(r"\b(segment|vertical|division|business(es)?|category|fmeg|epc|jewell?ery|subsidiary|this portfolio)\b", sent, re.I) \
                and not re.search(r"\b(overall|company|consolidated|total)\b", sent, re.I):
            basis = "segment"
        cond = None
        mc = re.search(r"\b(subject to|assuming|provided|barring|if)\b[^.,;]*", sent, re.I)
        if mc:
            cond = mc.group(0)[:200]
        if inferred:
            cond = (cond + "; " if cond else "") + inferred
        cand = GuidanceCandidate(metric=metric, guidance_type="qualitative", statement=parent[:600], basis=basis,
                                 horizon=horizon, target_period=period, conditions=cond)
        if metric == "capex":
            m = INR_AMOUNT.search(sent)
            if m:
                cand.guidance_type, cand.unit, cand.comparator = "quantitative", "crore", "="
                cand.target_value = finance.to_crore(float(m.group(1).replace(",", "")), m.group(2))
        else:
            r = PCT_RANGE.search(sent)
            one = PCT_ONE.search(sent)
            if r and float(r.group(1)) < float(r.group(2)):
                cand.guidance_type, cand.unit, cand.comparator = "quantitative", "pct", "range"
                cand.target_min, cand.target_max = float(r.group(1)), float(r.group(2))
            elif one:
                v = float(one.group(1))
                plus = bool(one.group(2)) or bool(re.search(r"(at least|minimum|or more|above|over)\s+\d", sent, re.I))
                cand.guidance_type, cand.unit = "quantitative", "pct"
                cand.target_value, cand.comparator = v, ">=" if plus else "="
                if re.search(r"(up to|upto|at most|below|under|not more than)\s+\d", sent, re.I):
                    cand.comparator = "<="
            else:
                for rx, (lo, hi) in WORD_RANGES.items():
                    if re.search(rx, sent, re.I):
                        cand.guidance_type, cand.unit = "quantitative", "pct"
                        cand.target_min, cand.target_max = lo, hi
                        cand.comparator = "range" if hi else ">="
                        if hi is None:
                            cand.target_value = lo
                        break
        lo_hi = PLAUSIBLE.get(metric)
        if lo_hi and cand.guidance_type == "quantitative" and cand.unit == "pct":
            vals = [v for v in (cand.target_value, cand.target_min, cand.target_max) if v is not None]
            if vals and not all(lo_hi[0] <= v <= lo_hi[1] for v in vals):
                continue
            # A margin "target" of 1-2% from a company that guides 20%+ is almost always a misattributed number.
            if inferred and metric in ("ebit_margin", "ebitda_margin") and max(vals or [0]) < 8:
                continue
        # Unspecified-metric numbers are kept only when explicitly framed as guidance/targets; never auto-compared.
        if metric.endswith("_unspecified") and cand.guidance_type == "quantitative" and not re.search(r"guid|target|aspir|expect", sent, re.I):
            continue
        out.append(cand)
    return out


# ---------------------------------------------------------------- comparison


@dataclass
class Comparison:
    status: str  # met / missed / tracking_on / tracking_below / pending / long_term / not_comparable / unavailable
    actual_value: float | None = None
    actual_label: str | None = None
    actual_period: str | None = None
    gap_pp: float | None = None
    method: str | None = None
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


Series = dict[str, float | None]  # fiscal_period -> value


def _margin_series(metric: str, revenue: Series, op: Series, dep: Series, opm: Series) -> tuple[Series, str]:
    out: Series = {}
    if metric == "ebitda_margin":
        for p, v in opm.items():
            out[p] = v
        return out, "OPM % (operating profit before other income ÷ revenue) as an EBITDA-margin proxy"
    for p in revenue:
        c = finance.margin_pct(None if op.get(p) is None or dep.get(p) is None else op[p] - dep[p], revenue.get(p))
        out[p] = c.value
    return out, "EBIT margin derived as (operating profit − depreciation) ÷ revenue"


def _hit(actual: float, g) -> bool:
    if g.comparator == "range":
        # For growth and margin promises, landing above the range is delivery, not a miss.
        return g.target_min is None or actual >= g.target_min
    if g.comparator == "<=":
        return actual <= g.target_value
    if g.comparator == ">=":
        return actual >= g.target_value
    # "=" : treat as a point estimate; within 0.5pp counts as delivered.
    return abs(actual - g.target_value) <= 0.5 or actual >= g.target_value


def _anchor(g) -> float | None:
    if g.comparator == "range":
        return g.target_min if g.target_min is not None else g.target_max
    return g.target_value


def compare(g, quarterly: dict[str, Series], annual: dict[str, Series], *, is_financial: bool, today: date) -> Comparison:
    """g: ManagementGuidance-like object. quarterly/annual: metric_name -> {period: value}."""
    if g.guidance_type != "quantitative":
        return Comparison("unavailable", caveats=["Qualitative statement — no number to check."])
    if g.metric in ("margin_unspecified", "growth_unspecified", "loan_growth", "volume_growth", "pat_margin"):
        return Comparison("not_comparable", caveats=[f"Metric '{g.metric}' is not mapped to a reported line item."])
    if g.metric == "capex":
        return Comparison("unavailable", caveats=["Capex is not broken out in the structured results data."])
    if is_financial and g.metric in ("ebitda_margin", "ebit_margin"):
        return Comparison("not_comparable", caveats=["EBITDA/EBIT is not meaningful for a lender."])
    if g.basis == "segment":
        return Comparison("not_comparable", caveats=["Guidance refers to a single business/segment; company-wide reported numbers are not a like-for-like check."])
    if g.basis == "constant_currency":
        cav = ["Guidance is in constant currency; reported INR figures include currency effects — not a like-for-like check."]
    else:
        cav = []

    anchor = _anchor(g)
    if anchor is None:
        return Comparison("unavailable", caveats=["Target value missing."])

    # Choose the actual series.
    if g.metric == "revenue_growth":
        rev_a = annual.get("revenue", {})
        label = "Revenue growth YoY (reported INR)"
        target = g.target_period
        if g.horizon == "long_term" or not target:
            latest = sorted(p for p in rev_a if p.startswith("FY"))
            if len(latest) >= 2:
                c = finance.growth_pct(rev_a[latest[-1]], rev_a[latest[-2]])
                return Comparison("long_term", c.value, label, latest[-1], method="Latest full-year growth shown for context only",
                                  caveats=cav + ["No dated target — shown for context, not scored."])
            return Comparison("unavailable", caveats=["No annual revenue history."])
        if target.startswith("Q"):
            return Comparison("not_comparable", caveats=["Quarter-specific growth targets are not scored yet."])
        fy = int(target[2:]) + 2000
        cur, prev = fy_label(fy), fy_label(fy - 1)
        if rev_a.get(cur) is not None and rev_a.get(prev) is not None:
            c = finance.growth_pct(rev_a[cur], rev_a[prev])
            if c.value is None:
                return Comparison("unavailable", caveats=[c.note or ""])
            status = "not_comparable" if cav else ("met" if _hit(c.value, g) else "missed")
            return Comparison(status, round(c.value, 2), label, cur, round(c.value - anchor, 2),
                              "Full-year revenue vs prior full year", cav)
        # Fiscal year in progress: year-to-date vs same quarters last year.
        rev_q = quarterly.get("revenue", {})
        ytd_now, ytd_prev, used = 0.0, 0.0, []
        for q in range(1, 5):
            a, b = FiscalQuarter(fy, q).label, FiscalQuarter(fy - 1, q).label
            if rev_q.get(a) is not None and rev_q.get(b) is not None:
                ytd_now += rev_q[a]
                ytd_prev += rev_q[b]
                used.append(a)
        if used:
            c = finance.growth_pct(ytd_now, ytd_prev)
            on = _hit(c.value, g) if c.value is not None else False
            return Comparison("not_comparable" if cav else ("tracking_on" if on else "tracking_below"),
                              round(c.value, 2) if c.value is not None else None, label + " — year to date",
                              "+".join(used), round(c.value - anchor, 2) if c.value is not None else None,
                              f"YTD ({len(used)} quarters) vs same quarters last year; target year not complete",
                              cav + ["Interim read — the fiscal year is not complete."])
        return Comparison("pending", caveats=cav + [f"No reported results yet for {target}."])

    # Margins
    series_q, method = _margin_series(g.metric, quarterly.get("revenue", {}), quarterly.get("operating_profit", {}),
                                      quarterly.get("depreciation", {}), quarterly.get("opm_pct", {}))
    series_a, _ = _margin_series(g.metric, annual.get("revenue", {}), annual.get("operating_profit", {}),
                                 annual.get("depreciation", {}), annual.get("opm_pct", {}))
    label = "EBITDA margin (proxy)" if g.metric == "ebitda_margin" else "EBIT margin (derived)"
    cav = cav + [method]
    target = g.target_period
    latest_q = sorted((p for p, v in series_q.items() if v is not None), key=lambda p: (int(p[4:]), int(p[1])))
    if g.horizon == "long_term" or not target:
        if latest_q:
            p = latest_q[-1]
            return Comparison("long_term", round(series_q[p], 2), label, p, round(series_q[p] - anchor, 2),
                              "Latest quarter vs long-term aspiration (context, not scored)", cav)
        return Comparison("unavailable", caveats=cav)
    if target.startswith("Q"):
        v = series_q.get(target)
        if v is not None:
            return Comparison("met" if _hit(v, g) else "missed", round(v, 2), label, target, round(v - anchor, 2),
                              "Target quarter actual", cav)
        if latest_q:
            p = latest_q[-1]
            v = series_q[p]
            return Comparison("tracking_on" if _hit(v, g) else "tracking_below", round(v, 2), label + " — latest quarter",
                              p, round(v - anchor, 2), f"Latest reported quarter; target is {target}",
                              cav + ["Target period not reached yet."])
        return Comparison("pending", caveats=cav)
    v = series_a.get(target)
    if v is not None:
        return Comparison("met" if _hit(v, g) else "missed", round(v, 2), label, target, round(v - anchor, 2),
                          "Full-year actual", cav)
    fy = int(target[2:]) + 2000
    inyear = [p for p in latest_q if p.endswith(f"FY{fy % 100:02d}")]
    if inyear:
        p = inyear[-1]
        v = series_q[p]
        return Comparison("tracking_on" if _hit(v, g) else "tracking_below", round(v, 2), label + " — latest quarter",
                          p, round(v - anchor, 2), "Latest quarter in the target year", cav + ["Fiscal year not complete."])
    return Comparison("pending", caveats=cav + [f"No reported results yet for {target}."])
