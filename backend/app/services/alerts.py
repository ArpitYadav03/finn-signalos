"""Alert rules: validated schema, deterministic evaluation, natural-language parsing.

The LLM (if any) only *translates* text into an AlertRuleSpec once, at creation time. Evaluation never
calls a model: it is plain comparisons over stored EOD bars and analysed filings.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.domain.categories import Category
from app.models import Alert, AlertRule, Announcement, Company
from app.services import market as market_svc

MARKET_METRICS = {"daily_return_pct", "abs_daily_return_pct", "volume_ratio"}
LEVEL_RANK = {"routine": 0, "low": 1, "medium": 2, "high": 3}


ROLE_GROUPS = {
    "CEO": {"CEO", "MD & CEO", "Managing Director"},
    "CFO": {"CFO"},
    "MD": {"Managing Director", "MD & CEO"},
    "Chairperson": {"Chairperson"},
    "COO": {"COO"},
    "Executive Director": {"Executive Director", "Whole-time Director"},
}


class Condition(BaseModel):
    metric: Literal["daily_return_pct", "abs_daily_return_pct", "volume_ratio", "announcement_category"]
    operator: Literal["gt", "lt", "gte", "lte", "in"]
    value: float | list[str]
    # Optional refinements for filing conditions: keep the user's intent precise.
    roles: list[str] | None = None                         # e.g. ["CEO", "CFO"] for leadership changes
    rating_action: Literal["downgrade", "upgrade"] | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.roles is not None:
            bad = [r for r in self.roles if r not in ROLE_GROUPS]
            if bad or not self.roles:
                raise ValueError(f"Unknown roles {bad}; use {sorted(ROLE_GROUPS)}")
        if self.metric == "announcement_category":
            if self.operator != "in" or not isinstance(self.value, list) or not self.value:
                raise ValueError("announcement_category needs operator 'in' and a non-empty list")
            valid = {c.value for c in Category}
            bad = [v for v in self.value if v not in valid]
            if bad:
                raise ValueError(f"Unknown categories: {bad}")
        else:
            if self.operator == "in" or isinstance(self.value, list):
                raise ValueError(f"{self.metric} needs a numeric comparison")
            if self.metric == "volume_ratio" and not (0 < float(self.value) <= 50):
                raise ValueError("volume_ratio threshold must be between 0 and 50")
            if "return" in self.metric and not (-50 <= float(self.value) <= 50):
                raise ValueError("price-move threshold must be within ±50%")
        return self


class AlertRuleSpec(BaseModel):
    ticker: str | None = Field(None, description="NSE symbol; null = whole portfolio")
    conditions: list[Condition] = Field(min_length=1, max_length=4)
    logical_operator: Literal["AND", "OR"] = "OR"
    min_importance: Literal["routine", "low", "medium", "high"] = "medium"
    enabled: bool = True

    @model_validator(mode="after")
    def _no_mixed_and(self):
        kinds = {"market" if c.metric in MARKET_METRICS else "announcement" for c in self.conditions}
        if self.logical_operator == "AND" and len(kinds) > 1:
            raise ValueError("AND across price and announcement conditions is not supported; use OR or two rules")
        return self

    @property
    def rule_type(self) -> str:
        kinds = {"market" if c.metric in MARKET_METRICS else "announcement" for c in self.conditions}
        return kinds.pop() if len(kinds) == 1 else "mixed"


def describe(spec: AlertRuleSpec) -> str:
    parts = []
    for c in spec.conditions:
        if c.metric == "daily_return_pct":
            parts.append(f"day move {'<' if c.operator in ('lt', 'lte') else '>'} {float(c.value):+g}%")
        elif c.metric == "abs_daily_return_pct":
            parts.append(f"day move beyond ±{float(c.value):g}%")
        elif c.metric == "volume_ratio":
            parts.append(f"volume > {float(c.value):g}× 20-day avg")
        else:
            parts.append("filing in " + ", ".join(c.value))
    who = spec.ticker or "Any holding"
    return f"{who}: " + f" {spec.logical_operator.lower()} ".join(parts)


FILING_PHRASE = {
    "results": "results", "M&A": "an M&A deal", "fund_raise": "a fund-raise", "management_change": "a leadership change",
    "litigation": "a legal or regulatory matter", "insider_trading": "an insider trade", "earnings_call": "an earnings-call update",
    "investor_meetings": "an investor-meeting notice", "credit_rating": "a credit-rating update", "dividend": "a dividend",
    "bonus_split": "a bonus or split", "capex": "a capex plan", "press_release": "a press release",
    "postal_ballot": "a postal-ballot result", "agm_egm": "an AGM/EGM notice", "pledging": "a promoter pledge update",
}


def clauses_human(spec: AlertRuleSpec) -> list[str]:
    """Each condition as a short plain-English phrase, e.g. 'falls more than 4% in a day'."""
    from app.domain.categories import CATEGORY_LABELS

    out = []
    for c in spec.conditions:
        if c.metric == "daily_return_pct":
            v = abs(float(c.value))
            strict = c.operator in ("gt", "lt")
            falling = c.operator in ("lt", "lte") or float(c.value) < 0
            out.append(f"{'falls' if falling else 'rises'} {'more than' if strict else 'at least'} {v:g}% in a day")
        elif c.metric == "abs_daily_return_pct":
            out.append(f"moves more than {float(c.value):g}% either way in a day")
        elif c.metric == "volume_ratio":
            out.append(f"volume exceeds {float(c.value):g}× its 20-day average")
        else:
            labels = []
            for v in c.value:
                if v == "management_change" and c.roles:
                    labels.append("a leadership change involving the " + " or ".join(c.roles))
                elif v == "credit_rating" and c.rating_action:
                    labels.append(f"a credit-rating {c.rating_action}")
                else:
                    labels.append(FILING_PHRASE.get(v, CATEGORY_LABELS[Category(v)].lower()))
            joined = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]
            out.append(f"files {joined}" + (" (high importance only)" if spec.min_importance == "high" else ""))
    return out


def describe_human(spec: AlertRuleSpec, company_name: str | None = None) -> str:
    who = company_name or "any of your holdings"
    return f"{who} " + f" {spec.logical_operator.lower()} ".join(clauses_human(spec))


def preview(spec: AlertRuleSpec, company_name: str | None, holdings: int) -> dict:
    """Everything the user should see before saving: scope, each condition, how they combine, when it's checked."""
    from app.core.config import get_settings

    n = len(spec.conditions)
    return {
        "scope": company_name or f"All {holdings} holdings",
        "conditions": clauses_human(spec),
        "combine": None if n == 1 else ("Alerts when ANY one of these happens" if spec.logical_operator == "OR"
                                        else "Alerts only when ALL of these happen on the same day"),
        "frequency": f"Checked after each refresh ({' and '.join(get_settings().daily_refresh_times_ist.split(','))} IST) "
                     "against the latest daily close and new filings",
        "min_importance": spec.min_importance,
    }


def unsupported_in(text: str) -> list[str]:
    t = (text or "").lower()
    return [label for rx, label in UNSUPPORTED if re.search(rx, t)]


def _cmp(v: float, op: str, t: float) -> bool:
    return {"gt": v > t, "lt": v < t, "gte": v >= t, "lte": v <= t}[op]


def _market_value(snap: dict, metric: str) -> float | None:
    if metric == "volume_ratio":
        return snap["volume_ratio"]["value"]
    r = snap["return_pct"]["value"]
    if r is None:
        return None
    return abs(r) if metric == "abs_daily_return_pct" else r


def _save(db: Session, alert: Alert) -> bool:
    try:
        with db.begin_nested():
            db.add(alert)
        return True
    except IntegrityError:  # dedupe_key already exists: the same rule already fired for this event/day
        return False


def _record(db: Session, key: str, value: dict) -> None:
    from app.models import UserPreference

    row = db.scalars(select(UserPreference).where(UserPreference.key == key)).first()
    if row is None:
        db.add(UserPreference(key=key, value=value))
    else:
        row.value = value


def evaluation_log(db: Session) -> dict:
    from app.models import UserPreference

    row = db.scalars(select(UserPreference).where(UserPreference.key == "alert_evaluations")).first()
    return dict(row.value) if row else {"rules": {}}


def evaluate_rule(db: Session, rule: AlertRule, companies: list[Company]) -> int:
    spec = AlertRuleSpec.model_validate(rule.parameters)
    checked_sessions: set[str] = set()
    matched_tickers: list[str] = []
    scope = list({c.id: c for c in companies if rule.company_id is None or c.id == rule.company_id}.values())  # each company once
    fired = 0
    mkt_conds = [c for c in spec.conditions if c.metric in MARKET_METRICS]
    ann_conds = [c for c in spec.conditions if c.metric == "announcement_category"]
    unable: list[str] = []
    for co in scope:
        if mkt_conds:
            snap = market_svc.snapshot(db, co.id)
            if snap is None:
                unable.append(f"{co.ticker}: no price data")
            elif all(_market_value(snap, c.metric) is None for c in mkt_conds):
                note = snap["return_pct"]["note"] or snap["volume_ratio"]["note"] or "value unavailable"
                unable.append(f"{co.ticker}: {note}")
            if snap:
                checked_sessions.add(snap["trading_date"])
                results = []
                for c in mkt_conds:
                    v = _market_value(snap, c.metric)
                    results.append(v is not None and _cmp(v, c.operator, float(c.value)))
                hit = all(results) if spec.logical_operator == "AND" else any(results)
                if hit:
                    matched_tickers.append(co.ticker)
                    ret, vr = snap["return_pct"]["value"], snap["volume_ratio"]["value"]
                    bits = []
                    if ret is not None:
                        bits.append(f"{ret:+.1f}% on the day")
                    if vr is not None:
                        bits.append(f"volume {vr:.1f}× 20-day avg")
                    fired += _save(db, Alert(
                        rule_id=rule.id, company_id=co.id, alert_type="market",
                        message=f"{co.ticker}: " + ", ".join(bits) + f" (close ₹{snap['close']:,.2f}, {snap['trading_date']})",
                        details={"snapshot": {k: snap[k] for k in ("trading_date", "close", "return_pct", "volume_ratio")},
                                 "rule": describe(spec)},
                        as_of=date.fromisoformat(snap["trading_date"]),
                        dedupe_key=f"r{rule.id}:c{co.id}:{snap['trading_date']}"))
        if ann_conds:
            cats = {v for c in ann_conds for v in c.value}
            since = utcnow() - timedelta(days=7)
            anns = db.scalars(select(Announcement).where(Announcement.company_id == co.id, Announcement.published_at >= since,
                                                         Announcement.processing_status == "processed")).all()
            for a in anns:
                ea = a.analysis
                if not ea or ea.category not in cats or LEVEL_RANK[ea.importance_level] < LEVEL_RANK[spec.min_importance]:
                    continue
                if not _filters_match(ann_conds, ea):
                    continue
                matched_tickers.append(co.ticker)
                fired += _save(db, Alert(
                    rule_id=rule.id, company_id=co.id, related_event_id=a.id, alert_type="announcement",
                    message=f"{co.ticker}: {ea.headline}", details={"category": ea.category, "level": ea.importance_level,
                                                                   "rule": describe(spec)},
                    as_of=a.published_at.date(), dedupe_key=f"r{rule.id}:a{a.id}"))
    log = evaluation_log(db)
    matched_set = sorted(set(matched_tickers))
    status = "matched" if matched_set else ("unable" if mkt_conds and not ann_conds and len(unable) == len(scope) else "no_match")
    log.setdefault("rules", {})[str(rule.id)] = {
        "at": utcnow().isoformat() + "Z", "companies_checked": len(scope),
        "eod_session": max(checked_sessions) if checked_sessions else None,
        "matched": matched_set, "new_alerts": fired, "status": status, "unable": unable[:5],
    }
    _record(db, "alert_evaluations", log)
    db.commit()
    return fired


def _filters_match(conds: list[Condition], ea) -> bool:
    """Role / rating-action refinements: a CEO/CFO rule must not fire on a company-secretary change."""
    for c in conds:
        if ea.category not in c.value:
            continue
        if ea.category == "management_change" and c.roles:
            allowed = set().union(*(ROLE_GROUPS[r] for r in c.roles))
            if ea.facts.get("role") not in allowed:
                continue
        if ea.category == "credit_rating" and c.rating_action:
            if ea.facts.get("action") != c.rating_action:
                continue
        return True
    return False


def evaluate_all(db: Session) -> int:
    from app.ingestion.pipeline import portfolio_companies

    companies = portfolio_companies(db)
    extra = [r.company for r in db.scalars(select(AlertRule).where(AlertRule.company_id.is_not(None))).all()
             if r.company and r.company not in companies]
    total = 0
    rules = db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))).all()
    for rule in rules:
        total += evaluate_rule(db, rule, companies + extra)
    log = evaluation_log(db)
    sessions = [v.get("eod_session") for v in log.get("rules", {}).values() if v.get("eod_session")]
    log["last_run"] = {"at": utcnow().isoformat() + "Z", "rules_evaluated": len(rules), "new_alerts": total,
                       "eod_session": max(sessions) if sessions else None}
    _record(db, "alert_evaluations", log)
    db.commit()
    return total


# ------------------------------------------------------------------ natural language -> spec

_CATEGORY_WORDS = [
    (r"result|earnings|quarterly numbers", "results"),
    (r"acqui|merger|m&a|demerger|joint venture", "M&A"),
    (r"\bcfo\b|\bceo\b|resign|management change|leadership|director", "management_change"),
    (r"rating|downgrade|upgrade", "credit_rating"),
    (r"pledg", "pledging"),
    (r"litigation|penalt|regulatory|lawsuit|legal|sebi order", "litigation"),
    (r"dividend", "dividend"),
    (r"fund ?rais|\bqip\b|rights issue|preferential|buyback", "fund_raise"),
    (r"insider|promoter (buy|sell)", "insider_trading"),
    (r"capex|capacity|new plant", "capex"),
    (r"bonus|split", "bonus_split"),
]
_MULT_WORDS = {"twice": 2.0, "double": 2.0, "thrice": 3.0, "triple": 3.0}


class ParseResult(BaseModel):
    spec: AlertRuleSpec | None
    explanation: str
    method: str
    unparsed: list[str] = []
    unsupported: list[str] = []


UNSUPPORTED = [
    (r"intraday|intra-day|minute|hourly|real[- ]?time|live price", "intraday or real-time moves (FINN uses end-of-day data)"),
    (r"(?:below|above|under|over|crosses?|hits?)\s*(?:rs\.?|₹|inr)\s?\d", "absolute price levels"),
    (r"\brsi\b|moving average|\bsma\b|\bema\b|macd|bollinger", "technical indicators"),
    (r"52[- ]week", "52-week highs/lows"),
    (r"\bnews\b|tweet|social media|sentiment|media report", "news or social media (FINN uses company filings only)"),
    (r"price target|broker|analyst (?:rating|upgrade|downgrade)|consensus|estimate", "broker or analyst views"),
    (r"options|open interest|futures|\bf&o\b", "derivatives data"),
]
ROLE_WORDS = [(r"\bceo\b|chief executive", "CEO"), (r"\bcfo\b|chief financial", "CFO"),
              (r"\bmd\b|managing director", "MD"), (r"chair(?:man|person)", "Chairperson"), (r"\bcoo\b", "COO")]


def signature(spec: AlertRuleSpec) -> str:
    """Canonical form for equivalence: same company, same conditions (order-free, roles sorted), same AND/OR and filter."""
    import json as _json

    conds = sorted(_json.dumps({"m": c.metric, "o": c.operator, "v": sorted(c.value) if isinstance(c.value, list) else round(float(c.value), 4),
                                "r": sorted(c.roles) if c.roles else None, "ra": c.rating_action}, sort_keys=True) for c in spec.conditions)
    op = spec.logical_operator if len(conds) > 1 else "-"
    return _json.dumps([spec.ticker, conds, op, spec.min_importance])


def tighten(spec: AlertRuleSpec, text: str) -> AlertRuleSpec:
    """Keep the precision the user asked for, whatever a model proposed: roles ("CEO or CFO") and rating direction
    ("downgrade") named in their words are applied, and a broader unfiltered duplicate of a filtered filing condition is
    dropped — "any leadership change OR a CEO/CFO change" would otherwise fire on every leadership change."""
    t = (text or "").lower()
    roles = [r for rx, r in ROLE_WORDS if re.search(rx, t)]
    down, up = bool(re.search(r"downgrad", t)), bool(re.search(r"upgrad", t))
    conds = []
    for c in spec.conditions:
        upd: dict = {}
        if c.metric == "announcement_category" and isinstance(c.value, list):
            if c.value == ["management_change"] and roles and not c.roles:
                upd["roles"] = roles
            if c.value == ["credit_rating"] and c.rating_action is None and down != up:
                upd["rating_action"] = "downgrade" if down else "upgrade"
        conds.append(c.model_copy(update=upd) if upd else c)
    filtered = {cat for c in conds if c.metric == "announcement_category" and isinstance(c.value, list) and (c.roles or c.rating_action)
                for cat in c.value}
    out = []
    for c in conds:
        if c.metric == "announcement_category" and isinstance(c.value, list) and not (c.roles or c.rating_action):
            rest = [v for v in c.value if v not in filtered]
            if not rest:
                continue
            if rest != c.value:
                c = c.model_copy(update={"value": rest})
        if c not in out:
            out.append(c)
    return spec.model_copy(update={"conditions": out}) if out else spec


def parse_rule_text(text: str, companies: list[Company]) -> ParseResult:
    t = text.lower()
    ticker = None
    for c in companies:
        first = re.sub(r"\b(ltd|limited|the)\b", "", c.company_name.lower()).split()
        if re.search(rf"\b{re.escape(c.ticker.lower())}\b", t) or (first and len(first[0]) > 3 and re.search(rf"\b{re.escape(first[0])}\b", t)):
            ticker = c.ticker
            break
    conds: list[Condition] = []
    m = re.search(r"(fall|falls|drop|drops|declin\w*|down|lose|loses|slump\w*|crash\w*)\D{0,25}(\d+(?:\.\d+)?)\s*%", t)
    if m:
        conds.append(Condition(metric="daily_return_pct", operator="lt", value=-float(m.group(2))))
    m = re.search(r"(rise|rises|gain|gains|jump\w*|up|surge\w*|rall\w*)\D{0,25}(\d+(?:\.\d+)?)\s*%", t)
    if m:
        conds.append(Condition(metric="daily_return_pct", operator="gt", value=float(m.group(2))))
    m = re.search(r"(mov\w*|±|\+/-|either (way|direction)|swing\w*)\D{0,25}(\d+(?:\.\d+)?)\s*%", t)
    if m and not conds:
        conds.append(Condition(metric="abs_daily_return_pct", operator="gt", value=float(m.group(3))))
    m = re.search(r"volume\D{0,40}?(\d+(?:\.\d+)?)\s*(x|×|times)", t)
    if m:
        conds.append(Condition(metric="volume_ratio", operator="gt", value=float(m.group(1))))
    else:
        for w, v in _MULT_WORDS.items():
            if re.search(rf"volume.{{0,40}}{w}|{w}.{{0,30}}volume", t):
                conds.append(Condition(metric="volume_ratio", operator="gt", value=v))
                break
    unsupported = [label for rx, label in UNSUPPORTED if re.search(rx, t)]
    cats = [cat for rx, cat in _CATEGORY_WORDS if re.search(rx, t)]
    # Avoid reading "volume ... results" style noise: only add categories when the text talks about filings/events.
    if cats and not (len(conds) and re.search(r"%|volume", t) and not re.search(r"announce|filing|news|report|resign|rating|result|acqui|pledg|dividend", t)):
        roles = [r for rx, r in ROLE_WORDS if re.search(rx, t)]
        action = "downgrade" if re.search(r"downgrad", t) else ("upgrade" if re.search(r"upgrad", t) else None)
        plain_cats = [c for c in dict.fromkeys(cats) if not ((c == "management_change" and roles) or (c == "credit_rating" and action))]
        if "management_change" in cats and roles:
            conds.append(Condition(metric="announcement_category", operator="in", value=["management_change"], roles=roles))
        if "credit_rating" in cats and action:
            conds.append(Condition(metric="announcement_category", operator="in", value=["credit_rating"], rating_action=action))
        if plain_cats:
            conds.append(Condition(metric="announcement_category", operator="in", value=plain_cats))
    if not conds:
        why = ("FINN can't watch " + "; ".join(unsupported) + ". It can watch daily close moves, volume versus the 20-day "
               "average, and filing types (results, leadership changes, ratings, deals…).") if unsupported else \
            "Couldn't find a price %, volume multiple or filing type in that text."
        return ParseResult(spec=None, explanation=why, method="rules-v1", unsupported=unsupported)
    has_market = any(c.metric in MARKET_METRICS for c in conds)
    has_ann = any(c.metric == "announcement_category" for c in conds)
    op = "AND" if (" and " in f" {t} " and " or " not in f" {t} " and not (has_market and has_ann)) else "OR"
    spec = AlertRuleSpec(ticker=ticker, conditions=conds, logical_operator=op)
    name = next((c.company_name for c in companies if c.ticker == ticker), None)
    return ParseResult(spec=spec, explanation="Alert me when " + describe_human(spec, name), method="rules-v1", unsupported=unsupported)
