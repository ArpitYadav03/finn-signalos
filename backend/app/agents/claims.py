"""Claim-level references and validation for numbers in research answers.

Every structured figure a tool returns is registered as a *fact* (F#) with company, metric, value, sign, unit,
period, statement basis and source; every derived figure is a *calculation* (C#) that keeps its formula and the ids of
its inputs. Answers cite these ids, and the UI opens exactly the facts and calculations a sentence relies on.

The validator checks each number in an answer against the claims it cites (or, failing that, a claim whose metric is
named in the same clause): value within rounding, sign (including words such as "fell"), unit (%, pp, ₹ crore, ×),
period, statement basis and company must all agree. A number that merely appears somewhere in tool output does not
count. Unsupported sentences are removed and listed; causal language needs a cited document passage that mentions
the stated cause. These are deterministic string-and-number checks: they confirm that a figure is the cited figure,
not that an interpretation is wise.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

CRORE, PCT, PP, TIMES, RUPEE = "₹ crore", "%", "pp", "×", "₹"
CHANGE_MEASURES = {"yoy", "qoq", "growth", "cagr", "daily_return", "period_return", "pp_change"}


def indian(n: float, decimals: int = 0) -> str:
    """Indian digit grouping: 1,23,456.7"""
    s = f"{abs(n):,.{decimals}f}"
    whole, _, frac = s.partition(".")
    digits = whole.replace(",", "")
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = f"{head},{tail}"
    return whole + (f".{frac}" if frac else "")


def fmt(value: float | None, unit: str, *, signed: bool = False) -> str:
    """One formatting rule for every figure: explicit unit, true minus sign, sign shown for changes."""
    if value is None:
        return "—"
    neg = value < 0
    sign = "−" if neg else ("+" if signed and value > 0 else "")
    a = abs(value)
    if unit == CRORE:
        return f"{sign}₹{indian(a, 0 if a >= 100 else 2)} crore"
    if unit == PCT:
        return f"{sign}{a:.1f}%"
    if unit == PP:
        return f"{sign}{a:.1f} pp"
    if unit == TIMES:
        return f"{sign}{a:.2f}×"
    if unit == RUPEE:
        return f"{sign}₹{indian(a, 2)}"
    if unit == "days":
        return f"{sign}{a:.0f} days"
    if unit == "shares":
        return f"{indian(a)} shares"
    return f"{sign}{a:,.2f}"


METRIC_WORDS: dict[str, str] = {
    "revenue": r"revenue|sales|top[- ]line|income from operations|turnover|interest earned|interest income",
    "operating_profit": r"ebitda|operating profit",
    "pbt": r"\bpbt\b|profit before tax|pre-tax",
    "pat": r"\bpat\b|net profit|profit after tax|bottom[- ]line|\bprofits?\b|earnings",
    "nii": r"net interest income|\bnii\b",
    "cfo": r"\bcfo\b|operating cash|cash (?:flow )?from operations|cash generated from operat|operating activities|cash flow",
    "cash_conversion": r"cash conversion|cfo\s*/\s*pat|cfo[- ]to[- ]pat|conver(?:ts|ting|sion)",
    "fcf": r"free cash flow|\bfcf\b",
    "opm_pct": r"operating margin|ebitda margin|\bopm\b|margins?",
    "pat_margin_pct": r"pat margin|net margin|profit margin|margins?|pat ÷ interest earned",
    "borrowings": r"borrowings|debt",
    "equity": r"\bequity\b|net worth",
    "debt_to_equity": r"debt[- /]to[- ]equity|debt\s*/\s*equity|\bd/e\b|leverage|gearing",
    "roce_pct": r"\broce\b|return on capital",
    "roe_pct": r"\broe\b|return on equity",
    "working_capital_days": r"working[- ]capital days",
    "close": r"\bclos(?:e|ed|ing)\b|share price|\bprice\b|traded at",
    "volume_ratio": r"\bvolume\b|\bvol\b",
    # Price moves: named by price words, not by movement verbs ("PAT fell" is about PAT).
    "price_return": r"\bmoves?\b|\bstock\b|share price|price (?:change|move)|daily return|over the period",
}
NEG_WORDS = re.compile(r"\b(fell|fall|falls|falling|declin\w*|decreas\w*|drop\w*|contract\w*|lower|down|shr[iu]nk\w*|"
                       r"slip\w*|negative|loss|losses|weak\w*|compress\w*|dip\w*|plung\w*|slump\w*|sank|reduc\w*|outflow)\b", re.I)
POS_WORDS = re.compile(r"\b(rose|rise|rises|rising|grew|grow\w*|increas\w*|up|expand\w*|higher|jump\w*|gain\w*|improv\w*|"
                       r"climb\w*|surg\w*|advanc\w*)\b", re.I)
CAUSAL = re.compile(r"\b(because(?: of)?|due to|driven by|owing to|on account of|as a result of|resulting from|reflecting|"
                    r"led by|on the back of|thanks to|attributable to|caused by|amid|which (?:shows|indicates) that)\b", re.I)
STOP = {"which", "their", "there", "these", "those", "about", "after", "before", "under", "would", "could", "should",
        "while", "being", "other", "company", "quarter", "year", "years", "higher", "lower", "strong", "weak", "continued"}


@dataclass
class Claim:
    id: str
    kind: str                      # fact | calc
    company: str                   # NSE symbol
    metric: str                    # e.g. cfo, pat, cash_conversion, revenue
    label: str
    measure: str                   # level | yoy | qoq | growth | cagr | ratio | margin | daily_return | period_return | pp_change
    value: float
    unit: str
    period: str                    # FY26, Q2FY27, 2026-11-13, 2026-10-01→2026-11-13
    period_type: str               # annual | quarter | session | range
    basis: str | None = None       # consolidated | standalone
    base_period: str | None = None # comparison period for changes
    source: dict = field(default_factory=dict)
    formula: str | None = None
    expression: str | None = None
    inputs: list[str] = field(default_factory=list)
    display: str = ""

    def brief(self) -> str:
        extra = f" vs {self.base_period}" if self.base_period else ""
        how = f" = {self.expression}" if self.expression else ""
        return f"{self.id}: {self.company} {self.label} {self.period}{extra} = {self.display}{how} ({self.basis or 'n/a'})"


class ClaimRegistry:
    def __init__(self, company: str):
        self.company = company
        self.items: dict[str, Claim] = {}
        self._keys: dict[tuple, str] = {}
        self._n = {"fact": 0, "calc": 0}

    def _add(self, kind: str, key: tuple, **kw) -> str:
        if key in self._keys:
            return self._keys[key]
        self._n[kind] += 1
        cid = f"{'F' if kind == 'fact' else 'C'}{self._n[kind]}"
        signed = kw.get("measure") in CHANGE_MEASURES
        c = Claim(id=cid, kind=kind, company=self.company, display=fmt(kw["value"], kw["unit"], signed=signed), **kw)
        self.items[cid] = c
        self._keys[key] = cid
        return cid

    def fact(self, metric: str, label: str, value: float | None, unit: str, period: str, period_type: str,
             basis: str | None, source: dict, measure: str = "level") -> str | None:
        if value is None:
            return None
        return self._add("fact", ("f", metric, measure, period, basis, unit), metric=metric, label=label, measure=measure,
                         value=float(value), unit=unit, period=period, period_type=period_type, basis=basis, source=source)

    def calc(self, metric: str, label: str, value: float | None, unit: str, period: str, period_type: str,
             basis: str | None, measure: str, formula: str, inputs: list[str | None], expression: str,
             base_period: str | None = None, source: dict | None = None) -> str | None:
        if value is None or any(i is None for i in inputs):
            return None
        return self._add("calc", ("c", metric, measure, period, base_period, basis), metric=metric, label=label,
                         measure=measure, value=float(value), unit=unit, period=period, period_type=period_type, basis=basis,
                         base_period=base_period, formula=formula, expression=expression, inputs=[i for i in inputs if i],
                         source=source or {"kind": "calculation", "note": "Computed by FINN's finance module from the inputs listed."})

    def get(self, cid: str) -> Claim | None:
        return self.items.get(cid)

    def as_dict(self, ids: set[str] | None = None) -> dict:
        """Claims referenced by an answer plus their inputs (so a calculation always opens with what it was built from)."""
        if ids is None:
            keep = set(self.items)
        else:
            keep, todo = set(), [i for i in ids if i in self.items]
            while todo:
                i = todo.pop()
                if i in keep:
                    continue
                keep.add(i)
                todo += self.items[i].inputs
        return {i: asdict(self.items[i]) for i in sorted(keep, key=lambda x: (x[0], int(x[1:])))}


# ------------------------------------------------------------------ parsing numbers in prose

TOKEN = re.compile(
    r"(?<![\w.\[])(?P<sign>[-+−])?\s?(?P<cur>₹|rs\.?\s?|inr\s|us\$\s?|\$)?\s?(?P<sign2>[-+−])?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<unit>%|per ?cent\b|pp\b|percentage points?\b|bps\b|crores?\b|cr\b|lakhs?\b|lacs?\b|billion\b|bn\b|"
    r"million\b|mn\b|x\b|×|times\b))?", re.I)
PERIOD = re.compile(r"\b(Q[1-4])\s?FY\s?'?(\d{2,4})\b|\bFY\s?'?(\d{2,4})\b|\b(H[12])\s?FY\s?(\d{2})\b|\bfiscal (?:year )?(20\d{2})\b", re.I)
DATES = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(?:st|nd|rd|th)? (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*(?: \d{4})?\b|"
                   r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* \d{1,2},? \d{4}\b|\b\d{1,2}:\d{2}\b", re.I)
MARKER = re.compile(r"\[(E\d+|F\d+|C\d+|M)\]")


@dataclass
class Token:
    raw: str
    start: int
    value: float          # in the claim's comparable unit (crore for money)
    unit: str | None      # %, pp, ₹ crore, ×, ₹, usd, None
    sign: str | None      # "-", "+", or None
    decimals: int


def _norm_period(m: re.Match) -> str:
    q, fy, fy2, h, hfy, fiscal = m.groups()
    def yy(x: str) -> str:
        return x[-2:]
    if q:
        return f"{q.upper()}FY{yy(fy)}"
    if fy2:
        return f"FY{yy(fy2)}"
    if h:
        return f"{h.upper()}FY{yy(hfy)}"
    return f"FY{fiscal[-2:]}"


def shift_period(p: str, kind: str) -> str | None:
    """Q2FY27 -> year ago Q2FY26 / previous quarter Q1FY27; FY26 -> FY25."""
    m = re.fullmatch(r"Q([1-4])FY(\d{2})", p or "")
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        if kind == "year":
            return f"Q{q}FY{y - 1:02d}"
        return f"Q{q - 1}FY{y:02d}" if q > 1 else f"Q4FY{y - 1:02d}"
    m = re.fullmatch(r"FY(\d{2})", p or "")
    if m:
        return f"FY{int(m.group(1)) - 1:02d}"
    return None


YEAR_AGO = re.compile(r"a year (?:ago|earlier)|year[- ]ago|same (?:quarter|period) last year|last year|previous year|prior year|\bLY\b", re.I)
PREV_Q = re.compile(r"previous quarter|prior quarter|last quarter|preceding quarter|sequential(?:ly)?", re.I)


def tokens(text: str) -> list[Token]:
    out = []
    for m in TOKEN.finditer(text):
        raw = m.group(0).strip()
        try:
            v = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        unit_raw = (m.group("unit") or "").lower().strip()
        cur = (m.group("cur") or "").lower().strip()
        sign = m.group("sign") or m.group("sign2")
        sign = "-" if sign in ("-", "−") else "+" if sign == "+" else None
        decimals = len(m.group("num").split(".")[1]) if "." in m.group("num") else 0
        unit = None
        if unit_raw in ("%", "percent", "per cent"):
            unit = PCT
        elif unit_raw.startswith("percentage point") or unit_raw == "pp":
            unit = PP
        elif unit_raw == "bps":
            unit, v = PP, v / 100
        elif unit_raw in ("x", "×", "times"):
            unit = TIMES
        elif cur.startswith("us") or cur == "$":
            unit = "usd"
        elif unit_raw in ("crore", "crores", "cr"):
            unit = CRORE
        elif unit_raw in ("lakh", "lakhs", "lac", "lacs"):
            unit, v = CRORE, v / 100
        elif unit_raw in ("billion", "bn"):
            unit, v = (CRORE, v * 100) if cur else ("billion", v)
        elif unit_raw in ("million", "mn"):
            unit, v = (CRORE, v / 10) if cur else ("million", v)
        elif cur:
            unit = RUPEE
        # Trivial numbers: years, small counts without a unit, list indices.
        if unit is None and (1990 <= v <= 2040 or (v < 32 and decimals == 0)):
            continue
        out.append(Token(raw, m.start(), v, unit, sign, decimals))
    return out


def _word_sign(window: str) -> str | None:
    neg = [m.end() for m in NEG_WORDS.finditer(window)]
    pos = [m.end() for m in POS_WORDS.finditer(window)]
    if not neg and not pos:
        return None
    return "-" if (max(neg) if neg else -1) > (max(pos) if pos else -1) else "+"


def _metrics_named(text: str) -> set[str]:
    return {k for k, rx in METRIC_WORDS.items() if re.search(rx, text, re.I)}


def nearest_metric(before: str, after: str) -> set[str]:
    """The metric a figure belongs to: the metric word closest before it in its clause, else the first one after it."""
    best, ends = set(), -1
    for k, rx in METRIC_WORDS.items():
        last = None
        for m in re.finditer(rx, before, re.I):
            last = m.end()
        if last is not None:
            if last > ends:
                best, ends = {k}, last
            elif last == ends:
                best.add(k)
    if best:
        return best
    first, starts = set(), 10 ** 9
    for k, rx in METRIC_WORDS.items():
        m = re.search(rx, after, re.I)
        if m:
            if m.start() < starts:
                first, starts = {k}, m.start()
            elif m.start() == starts:
                first.add(k)
    return first


def _metric_compatible(c: Claim, named: set[str]) -> bool:
    if not named:
        return True
    fam = {c.metric}
    if c.metric in ("daily_return", "period_return"):
        fam |= {"price_return", "close"}
    if c.metric == "revenue":
        fam |= {"revenue"}
    if c.metric in ("opm_pct", "pat_margin_pct"):
        fam |= {"opm_pct", "pat_margin_pct"}
    return bool(fam & named)


@dataclass
class Check:
    ok: bool
    reason: str = ""


def check_token(tok: Token, c: Claim, *, window: str, local_period: str | None, sentence_periods: set[str],
                sentence: str, explicit: bool, named: set[str] | None = None, sign_text: str | None = None) -> Check:
    # unit
    if tok.unit == "usd" or tok.unit in ("billion", "million"):
        return Check(False, "foreign-currency or unscaled amount is not a structured ₹ figure")
    if tok.unit is not None and tok.unit != c.unit:
        if not (tok.unit == RUPEE and c.unit == CRORE and re.search(r"\bcr(?:ore)?s?\b", sentence, re.I)):
            return Check(False, f"unit {tok.unit} does not match {c.unit}")
    if tok.unit is None and c.unit in (PCT, PP):
        return Check(False, f"figure is missing its unit ({c.unit})")
    # value
    tol = 0.5 * 10 ** (-tok.decimals) + 1e-9
    if c.unit == CRORE and tok.decimals == 0:
        tol = max(tol, 0.5)
    if abs(abs(tok.value) - abs(c.value)) > tol:
        return Check(False, f"value {tok.raw} ≠ {c.display}")
    # sign
    st = sign_text if sign_text is not None else window
    stated = tok.sign or (_word_sign(st) if c.measure in CHANGE_MEASURES else
                          ("-" if re.search(r"\b(loss|negative|outflow)\b", st, re.I) else None))
    if c.value < 0 and abs(c.value) >= 0.05 and stated != "-":
        return Check(False, f"sign: {c.label} is negative ({c.display}) but the sentence states it as positive")
    if c.value > 0 and stated == "-":
        return Check(False, f"sign: {c.label} is positive ({c.display}) but the sentence states a decline/negative")
    # period
    if c.period_type in ("annual", "quarter"):
        if local_period and local_period not in (c.period, c.base_period):
            return Check(False, f"period {local_period} ≠ {c.period}")
        rel = {shift_period(p, k) for p in sentence_periods for k in ("year", "quarter")}
        if not local_period and sentence_periods and not ({c.period, c.base_period} & (sentence_periods | rel)):
            return Check(False, f"period {', '.join(sorted(sentence_periods))} ≠ {c.period}")
    # basis
    if c.basis == "consolidated" and re.search(r"\bstandalone\b", sentence, re.I) and not re.search(r"\bconsolidated\b", sentence, re.I):
        return Check(False, "basis: sentence says standalone, figure is consolidated")
    if c.basis == "standalone" and re.search(r"\bconsolidated\b", sentence, re.I) and not re.search(r"\bstandalone\b", sentence, re.I):
        return Check(False, "basis: sentence says consolidated, figure is standalone")
    # metric
    if named is None:
        named = _metrics_named(window) or _metrics_named(sentence)
    if not _metric_compatible(c, named):
        return Check(False, f"metric: the sentence describes {', '.join(sorted(named))}, the figure is {c.label}")
    if not explicit and not named:
        return Check(False, "no metric named next to the figure")
    return Check(True)


def _passage_supports(tok: Token, passage: str) -> bool:
    """A document-sourced figure must appear in the cited passage with a compatible unit."""
    for t in tokens(re.sub(r"\s+", " ", passage)):
        tol = 0.5 * 10 ** (-tok.decimals) + 1e-9
        if abs(abs(t.value) - abs(tok.value)) > max(tol, 1e-9) and not (tok.unit == CRORE and abs(t.value - tok.value) <= 0.5):
            continue
        if tok.unit in (PCT, PP) and t.unit not in (PCT, PP):
            continue
        if tok.unit == CRORE and t.unit not in (CRORE, None, RUPEE):
            continue
        if tok.unit == "usd" and t.unit not in ("usd", "billion", "million", None):
            continue
        return True
    return False


def _content_words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


@dataclass
class Report:
    removed: list[dict] = field(default_factory=list)          # {"text", "reason"}
    explanations_removed: list[dict] = field(default_factory=list)
    explanations_supported: list[dict] = field(default_factory=list)
    resolved: int = 0
    checked: int = 0
    insufficient: bool = False

    def caveats(self) -> list[str]:
        out = []
        if self.removed:
            out.append(f"Removed {len(self.removed)} statement(s) whose figures did not match a cited source on value, sign, "
                       "unit, period, basis and company: " + "; ".join(f"“{r['text'][:90]}” ({r['reason']})" for r in self.removed[:4]))
        if self.explanations_removed:
            out.append("Removed explanation(s) that no retrieved passage supports — the observed figures stand, the stated cause "
                       "does not: " + "; ".join(f"“…{r['text'][:80]}”" for r in self.explanations_removed[:4]))
        return out


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(*\[])|(?<=[.!?]\])\s+")


def verify(answer: str, reg: ClaimRegistry, passage_text, *, other_entities: list[str] | None = None,
           important_min_chars: int = 40) -> tuple[str, Report]:
    """Check every figure and every causal clause; drop what is unsupported. `passage_text(eid)` returns the full text
    of an evidence passage (or "")."""
    rep = Report()
    # "[F13, F14]" / "[E2; C4]" -> "[F13][F14]" so every reference is checked and becomes its own control.
    answer = re.sub(r"\[\s*((?:[EFC]\d+\s*[,;]\s*)+[EFC]\d+)\s*\]",
                    lambda m: "".join(f"[{x.strip()}]" for x in re.split(r"[,;]", m.group(1))), answer)
    other_rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in other_entities) + r")\b", re.I) if other_entities else None
    out_lines = []
    for line in answer.split("\n"):
        if not line.strip() or re.match(r"^\s*(\*\*)?(limits?|limitations|caveats?)(\*\*)?:?", line, re.I):
            out_lines.append(line)
            continue
        prefix = re.match(r"^\s*(?:[-*•]|\d+\.)\s+(?:\*\*[^*]{1,60}\*\*:?\s*)?", line)
        pre = prefix.group(0) if prefix else ""
        body = line[len(pre):]
        kept = []
        line_markers = MARKER.findall(body)
        for sent in SENT_SPLIT.split(body):
            if not sent.strip():
                continue
            new = _verify_sentence(sent, reg, passage_text, rep, other_rx, inherited=line_markers, label=pre)
            if new:
                kept.append(new)
        if kept:
            out_lines.append(pre + " ".join(kept))
        elif not pre and not body.strip():
            out_lines.append(line)
    text = "\n".join(out_lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # Replace the answer only when nothing supported is left: no cited statement survives, or almost no text does.
    substantive = re.sub(r"\[(E|F|C)\d+\]|\*\*[^*]*\*\*:?|[-*•]\s", "", text)
    if rep.removed and (not MARKER.search(text) or len(substantive.strip()) < 40):
        rep.insufficient = True
    return text, rep


def _verify_sentence(sent: str, reg: ClaimRegistry, passage_text, rep: Report, other_rx,
                     inherited: list[str] | None = None, label: str = "") -> str | None:
    # A bullet's bold label ("**Interest earned:** Reported at ₹55,992 crore") names the metric for its sentences.
    label_metrics = _metrics_named(re.sub(r"[*:\-•]", " ", label or ""))
    # A sentence inside a quoted passage or a bullet often carries no marker of its own: the line's markers apply.
    markers = MARKER.findall(sent) or list(inherited or [])
    e_refs = [m for m in markers if m.startswith("E")]
    fc_refs = [m for m in markers if m[0] in "FC" and reg.get(m)]
    plain = MARKER.sub(lambda m: " " * len(m.group(0)), sent)          # keep offsets stable
    plain_nodates = DATES.sub(lambda m: " " * len(m.group(0)), plain)
    periods = [(m.start(), _norm_period(m)) for m in PERIOD.finditer(plain)]
    period_set = {p for _, p in periods}
    scan = PERIOD.sub(lambda m: " " * len(m.group(0)), plain_nodates)   # don't read "26" in FY26 as a figure
    toks = tokens(scan)
    added: list[str] = []
    for i, tok in enumerate(toks):
        rep.checked += 1
        end = tok.start + len(tok.raw)
        prev_end = toks[i - 1].start + len(toks[i - 1].raw) if i else 0
        clause_start = max(scan.rfind(";", 0, tok.start), scan.rfind(":", 0, tok.start), 0)
        nxt = [x for x in (scan.find(";", end), toks[i + 1].start if i + 1 < len(toks) else -1) if x != -1]
        clause_end = min(nxt + [len(scan), end + 30])
        before, after = plain[clause_start: tok.start], plain[end: clause_end]
        window = plain[max(clause_start, prev_end): clause_end]
        near_m = nearest_metric(before, after)
        named = near_m or label_metrics or _metrics_named(plain)
        # Direction words: those before the figure in its clause ("fell 2.0 pp"), else right after it ("6.6% lower").
        sb = plain[max(clause_start, prev_end): tok.start]
        sign_text = sb if _word_sign(sb) else plain[end: end + 15]
        # Period: one written right after the figure ("18.0% in Q2FY26") wins; else the nearest one before it.
        after_p = next((p for pos, p in periods if end <= pos <= end + 18 and re.match(r"\s*(?:in|for|during|of|,)?\s*$",
                                                                                         plain[end:pos], re.I)), None)
        local = after_p or next((p for pos, p in reversed(periods) if pos < tok.start and pos >= clause_start - 40), None)
        # Relative periods ("₹8,531 crore a year ago", "21.0% in the previous quarter") resolve against the stated period.
        near = plain[max(prev_end, tok.start - 30): clause_end]
        anchor = local or (sorted(period_set)[-1] if len(period_set) == 1 else None)
        if anchor and not after_p:
            if YEAR_AGO.search(near) and not re.search(r"\byo?y\b|year[- ]on[- ]year", plain[end: end + 12], re.I):
                local = shift_period(anchor, "year") or local
            elif PREV_Q.search(near) and not re.search(r"\bqoq\b|quarter[- ]on[- ]quarter", plain[end: end + 12], re.I):
                local = shift_period(anchor, "quarter") or local
        if other_rx and other_rx.search(plain):
            rep.removed.append({"text": _clean(sent), "reason": f"names another company ({other_rx.search(plain).group(0)})"})
            return None
        ok, reason = False, "no cited source contains this figure with the same metric, period and unit"
        for cid in fc_refs:
            chk = check_token(tok, reg.get(cid), window=window, local_period=local, sentence_periods=period_set,
                              sentence=plain, explicit=True, named=named, sign_text=sign_text)
            if chk.ok:
                ok = True
                break
            reason = chk.reason
        if not ok and e_refs and any(_passage_supports(tok, passage_text(e) or "") for e in e_refs):
            ok = True
        if not ok:
            # No (matching) explicit reference: resolve only against a claim whose metric, period, unit and sign all agree.
            for c in reg.items.values():
                if check_token(tok, c, window=window, local_period=local, sentence_periods=period_set, sentence=plain,
                               explicit=False, named=near_m or label_metrics, sign_text=sign_text).ok:
                    ok = True
                    rep.resolved += 1
                    if f"[{c.id}]" not in sent and c.id not in added:
                        added.append(c.id)
                    break
        if not ok:
            rep.removed.append({"text": _clean(sent), "reason": f"{tok.raw}: {reason}"})
            return None
    sent = re.sub(r"\[M\]", "", sent)
    if added:
        sent = _append_markers(sent, added)
    # Causal language needs a document passage that mentions the stated cause.
    m = CAUSAL.search(MARKER.sub("", sent))
    if m:
        bare = MARKER.sub("", sent)
        cause = bare[m.end():]
        cause = re.split(r"[.;]", cause)[0]
        words = _content_words(cause)
        support = [e for e in e_refs if words and _content_words(passage_text(e) or "") & words]
        if support:
            rep.explanations_supported.append({"text": _clean(cause), "evidence": support})
        else:
            rep.explanations_removed.append({"text": _clean(bare[m.start():].split(".")[0])})
            sent = _strip_cause(sent)
            if not sent:
                return None
    return re.sub(r"\s{2,}", " ", sent).strip()


def _append_markers(sent: str, ids: list[str]) -> str:
    tail = "".join(f"[{i}]" for i in ids)
    mm = re.search(r"([.!?])(\s*(?:\[[EFCM]\d*\])*)\s*$", sent)
    if mm:
        return sent[: mm.start()] + mm.group(2) + tail + mm.group(1)
    return sent + tail


def _strip_cause(sent: str) -> str:
    """'Margin contracted 4 pp, reflecting cost pressures [F2].' -> 'Margin contracted 4 pp [F2].'"""
    markers = "".join(f"[{m}]" for m in MARKER.findall(sent) if m != "M")
    bare = MARKER.sub("", sent)
    m = CAUSAL.search(bare)
    head = bare[: m.start()].rstrip(" ,;—–-:")
    if len(head.split()) < 3:
        return ""
    return head + (f" {markers}" if markers else "") + "."


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", MARKER.sub("", s)).strip()


INSUFFICIENT = ("**Insufficient evidence.** FINN could not match the figures needed for this answer to a cited source with "
                "the same metric, period, sign and unit, so it is not showing them. Try asking for a specific metric and period, "
                "or open the Financials tab.")
