"""Investor Handover: a saved monitoring plan, evaluated deterministically at each refresh, and the return briefing.

A plan is a validated spec (scope, typed conditions, AND/OR, the real refresh schedule). Evaluation reuses event
intelligence (grouped events, Event DNA roles, milestones), the Promise Ledger and EOD market snapshots. Each briefing
item has a stable id — event key + the set of filings it reflects — so refreshes and repeated requests never duplicate
items, and review progress is the explicit EventReview record (never inferred from viewing).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow

CondKind = Literal["filing", "guidance_cut", "fundraise_update", "price_move", "volume"]
REPEAT_ROLES = {"Repeat disclosure", "Repeat (newspaper copy)"}


class PlanCondition(BaseModel):
    kind: CondKind
    categories: list[str] | None = None        # filing: event categories
    roles: list[str] | None = None             # filing/management_change: CEO, CFO, MD, Chairperson, COO
    rating_action: Literal["downgrade", "upgrade"] | None = None
    change: Literal["departure", "appointment"] | None = None   # management_change: resignations/exits vs appointments
    direction: Literal["down", "up", "either"] | None = None   # price_move
    threshold: float | None = None             # price_move: % ; volume: × 20-session average

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "filing" and not self.categories:
            raise ValueError("filing condition needs categories")
        if self.kind in ("price_move", "volume") and (self.threshold is None or not 0 < self.threshold <= 50):
            raise ValueError(f"{self.kind} needs a threshold between 0 and 50")
        from app.services.alerts import ROLE_GROUPS
        from app.domain.categories import Category

        if self.roles and any(r not in ROLE_GROUPS for r in self.roles):
            raise ValueError(f"roles must be among {sorted(ROLE_GROUPS)}")
        if self.categories and any(x not in {c.value for c in Category} for x in self.categories):
            raise ValueError("unknown filing category")
        return self


class PlanSpec(BaseModel):
    tickers: list[str] | None = None           # None = all current holdings
    conditions: list[PlanCondition] = Field(min_length=1, max_length=8)
    logical_operator: Literal["OR", "AND"] = "OR"

    @model_validator(mode="after")
    def _and_only_market(self):
        if self.logical_operator == "AND" and any(c.kind not in ("price_move", "volume") for c in self.conditions):
            raise ValueError("AND is supported only between price and volume conditions on the same session")
        return self


def canonical(spec: PlanSpec) -> str:
    """Order-free identity of a plan: same scope, conditions and AND/OR → same plan."""
    return json.dumps({"t": sorted(spec.tickers or []), "op": spec.logical_operator if len(spec.conditions) > 1 else "OR",
                       "c": sorted(c.model_dump_json() for c in spec.conditions)}, sort_keys=True)


def describe(c: PlanCondition) -> str:
    if c.kind == "filing":
        cats = ", ".join(x.replace("_", " ") for x in c.categories or [])
        if c.categories == ["management_change"]:
            who = f"the {' or '.join(c.roles)}" if c.roles else "a director or key managerial person"
            what = {"departure": "departure (resignation, cessation or retirement) of", "appointment": "appointment of",
                    None: "leadership change involving"}[c.change]
            return f"a {what} {who}"
        if c.categories == ["credit_rating"] and c.rating_action:
            return f"a credit-rating {c.rating_action}"
        return f"a {cats} filing"
    if c.kind == "guidance_cut":
        return "management lowers a numeric guidance target (same measure, period and basis)"
    if c.kind == "fundraise_update":
        return "a fund raise is announced or moves to a new stage (approval, opening, allotment, completion)"
    if c.kind == "price_move":
        word = {"down": "falls more than", "up": "rises more than", "either": "moves more than ±"}[c.direction or "either"]
        return f"the stock {word} {c.threshold:g}% in a session (end-of-day close)"
    return f"volume exceeds {c.threshold:g}× its previous-20-session average (end-of-day)"


UNSUPPORTED = [
    (r"intraday|real[- ]time|live price|tick", "intraday or real-time prices (FINN uses end-of-day data)"),
    (r"\brsi\b|macd|moving average|technical", "technical indicators"),
    (r"news|tweet|social media|broker|analyst (rating|target)", "news, social media or broker views (primary sources only)"),
    (r"price (target|level)|crosses ₹|above ₹|below ₹", "absolute price levels"),
]


_COMMON = {"state", "bank", "india", "indian", "united", "national", "power", "life", "steel", "guidance", "watch", "cash", "asian",
           "general", "first", "great", "capital", "finance", "global", "new", "the"}


def parse_plan(text: str, companies) -> dict:
    """Plain words → plan. Returns {spec, explanation, unsupported, clarification}. One clarification at most, only when
    the plan would otherwise be wrong (a price move with no size)."""
    from app.services import alerts as A

    t = (text or "").lower()
    unsupported = [label for rx, label in UNSUPPORTED if re.search(rx, t)]
    conds: list[PlanCondition] = []
    tickers = None
    first = {}
    for c in companies:
        w = c.company_name.split()[0].lower()
        first.setdefault(w, []).append(c)
    named = [c for c in companies if re.search(rf"\b{re.escape(c.ticker.lower())}\b", t)
             or (len(w := c.company_name.split()[0].lower()) > 3 and w not in _COMMON and len(first[w]) == 1   # a distinctive name
                 and re.search(rf"\b{re.escape(w)}\b", t))]
    if named and not re.search(r"\b(my holdings|portfolio|all (my )?(stocks|companies|holdings))\b", t):
        tickers = sorted({c.ticker for c in named})
    if re.search(r"guidance (cut|reduc|lower|downgrad)|(cut|lower|reduc)\w* (its |their )?guidance|guidance cuts?", t):
        conds.append(PlanCondition(kind="guidance_cut"))
    if re.search(r"fund ?rais|fund-?raising|qip|rights issue|preferential|warrants?|capital raise", t):
        conds.append(PlanCondition(kind="fundraise_update"))
    roles = [r for rx, r in A.ROLE_WORDS if re.search(rx, t)]
    if roles or re.search(r"resign|leadership|management change|key managerial", t):
        change = ("departure" if re.search(r"resign|depart|exit|step(s|ping)? down|quit|leav|cessation|retire", t)
                  else "appointment" if re.search(r"\bappoint", t) else None)
        conds.append(PlanCondition(kind="filing", categories=["management_change"], roles=roles or None, change=change))
    if re.search(r"downgrad", t) and re.search(r"rating", t):
        conds.append(PlanCondition(kind="filing", categories=["credit_rating"], rating_action="downgrade"))
    for rx, cat in ((r"\bresults?\b|earnings", "results"), (r"acqui|merger|amalgamat", "M&A"), (r"litigation|penalt|court|tribunal", "litigation"),
                    (r"pledg", "pledging")):
        if re.search(rx, t) and not (cat == "results" and "guidance" in t and not re.search(r"\bresults\b", t)):
            conds.append(PlanCondition(kind="filing", categories=[cat]))
    pm = re.search(r"(fall|falls|drop|drops|declin\w*|rise|rises|jump\w*|mov\w*)[^.]{0,30}?(\d+(?:\.\d+)?)\s*%", t)
    clarification = None
    if pm:
        w = pm.group(1)
        direction = "down" if re.match(r"fall|drop|declin", w) else "up" if re.match(r"rise|jump", w) else "either"
        conds.append(PlanCondition(kind="price_move", direction=direction, threshold=float(pm.group(2))))
    elif re.search(r"\b(price|stock) (fall|drop|move|crash|spike)|falls? sharply|big (fall|move)", t):
        clarification = {"question": "How large a one-day move should count?", "field": "price_threshold",
                         "options": [3, 4, 5], "default": 4,
                         "why": "A price condition needs a size; FINN won't guess one silently."}
    vm = re.search(r"volume[^.]{0,30}?(\d+(?:\.\d+)?)\s*(x|×|times)", t)
    if vm:
        conds.append(PlanCondition(kind="volume", threshold=float(vm.group(1))))
    joiner = "AND" if (re.search(r"\band\b.*\bsame (day|session)\b|\bboth\b", t) and all(c.kind in ("price_move", "volume") for c in conds)
                       and len(conds) > 1) else "OR"
    # collapse equivalent conditions
    uniq, seen = [], set()
    for c in conds:
        k = c.model_dump_json()
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    if not uniq:
        return {"spec": None, "unsupported": unsupported, "clarification": clarification,
                "explanation": "FINN couldn't find a condition it can monitor in that description."}
    spec = PlanSpec(tickers=tickers, conditions=uniq, logical_operator=joiner)
    return {"spec": spec, "unsupported": unsupported, "clarification": clarification, "explanation": "Here's how FINN read it."}


def apply_clarification(spec: PlanSpec | None, text: str, answer: float) -> dict:
    from app.services.monitoring import PlanCondition as PC

    t = text.lower()
    direction = "down" if re.search(r"fall|drop|crash", t) else "up" if re.search(r"rise|jump|spike", t) else "either"
    conds = list(spec.conditions) if spec else []
    conds.append(PC(kind="price_move", direction=direction, threshold=float(answer)))
    return {"spec": PlanSpec(tickers=spec.tickers if spec else None, conditions=conds,
                             logical_operator=spec.logical_operator if spec else "OR"), "clarification": None}


def schedule_text() -> str:
    from app.core.config import get_settings

    return (f"Evaluated after each data refresh ({' and '.join(get_settings().daily_refresh_times_ist.split(','))} IST) "
            "against end-of-day prices and the latest filings FINN has indexed")


def preview(spec: PlanSpec, holdings: int) -> dict:
    return {"scope": ", ".join(spec.tickers) if spec.tickers else f"All {holdings} holdings",
            "conditions": [describe(c) for c in spec.conditions],
            "combine": None if len(spec.conditions) == 1 else ("FINN reports an item when ANY one of these happens" if spec.logical_operator == "OR"
                                                              else "Only when ALL of these happen in the same session"),
            "frequency": schedule_text()}


# ------------------------------------------------------------------ evaluation → return briefing

def _num_mid(t: str) -> float | None:
    n = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t or "")]
    return sum(n) / len(n) if n else None


def guidance_cuts(db: Session, company, since: datetime, as_of=None) -> list[dict]:
    """Strict: same named metric (not unspecified/capex), same explicit target period and basis, lower midpoint.
    `as_of` (a date) excludes anything stated later — replay must never see a later revision."""
    from app.models import ManagementGuidance as G

    q = select(G).where(G.company_id == company.id, G.guidance_type == "quantitative")
    if as_of is not None:
        q = q.where(G.guidance_date <= as_of)
    rows = db.scalars(q.order_by(G.guidance_date, G.source_page, G.id)).all()
    out = []
    for g in rows:
        if g.metric.endswith("_unspecified") or g.metric == "capex" or not g.target_period or not g.guidance_date:
            continue
        if datetime.combine(g.guidance_date, datetime.min.time()) < since:
            continue
        prev = [p for p in rows if p.metric == g.metric and p.target_period == g.target_period and p.basis == g.basis and p.unit == g.unit
                and p.guidance_date and p.guidance_date < g.guidance_date]
        if not prev:
            continue
        last_day = prev[-1].guidance_date
        p = min((x for x in prev if x.guidance_date == last_day), key=lambda x: (x.source_page or 0, x.id))   # first statement that day
        a = p.target_value if p.target_value is not None else ((p.target_min or 0) + (p.target_max or 0)) / 2 if p.target_min is not None else None
        b = g.target_value if g.target_value is not None else ((g.target_min or 0) + (g.target_max or 0)) / 2 if g.target_min is not None else None
        if a is None or b is None or b >= a:
            continue
        k = (g.metric, g.target_period, g.basis, g.guidance_date)
        if any((o["metric"], o["period"], o["basis"], o["date"]) == (k[0], k[1], k[2], k[3].isoformat()) for o in out):
            continue
        out.append({"guidance_id": g.id, "previous_id": p.id, "metric": g.metric, "period": g.target_period, "basis": g.basis,
                    "previous": _fmt_target(p), "current": _fmt_target(g), "previous_date": p.guidance_date.isoformat(),
                    "date": g.guidance_date.isoformat(), "statement": g.statement, "previous_statement": p.statement,
                    "chunk_id": g.chunk_id, "previous_chunk_id": p.chunk_id, "speaker": g.speaker,
                    "rule": "Same measure, target period and basis; the new midpoint is lower than the previous one"})
    return out


def _fmt_target(g) -> str:
    u = "%" if g.unit == "pct" else ""
    if g.target_min is not None and g.target_max is not None:
        return f"{g.target_min:g}–{g.target_max:g}{u}"
    return f"{g.target_value:g}{u}" if g.target_value is not None else "qualitative"


def _version(member_ids: list[int]) -> str:
    return hashlib.sha1(",".join(str(i) for i in sorted(member_ids)).encode()).hexdigest()[:12]


def _guidance_evidence(db: Session, gc: dict) -> list[dict]:
    from app.domain.evidence import document_claim

    out = []
    for cid, quote, label in ((gc["chunk_id"], gc["statement"], "Revised guidance"), (gc["previous_chunk_id"], gc["previous_statement"], "Previous guidance")):
        d = document_claim(db, cid, quote) if cid else None
        out.append({"label": label, "document": d.model_dump() if d else None,
                    "limitation": None if d else "The passage could not be matched to its page, so it is shown without a link."})
    return out


def evaluate(db: Session, plan, *, lookback_days: int = 30) -> dict:
    """Build the return briefing for one plan. Deterministic and read-only: viewing never marks anything reviewed."""
    from app.api.routes import _card, _event_dna, _iso, _milestones, _window_coverage
    from app.ingestion.pipeline import portfolio_companies
    from app.models import Announcement, Company, EventReview
    from app.services.market import snapshot
    from app.services.milestones import next_evidence, resolved_by

    spec = PlanSpec.model_validate(plan.spec)
    holdings = portfolio_companies(db)
    if spec.tickers:
        scope = db.scalars(select(Company).where(Company.ticker.in_(spec.tickers))).all()
    else:
        scope = holdings
    since = plan.created_at - timedelta(days=lookback_days)
    reviews = {r.event_key: r for r in db.scalars(select(EventReview)).all()}
    sections: dict[str, list] = {"new": [], "updated": [], "resolved": [], "unresolved": [], "unable": []}
    repeats_suppressed: list[dict] = []
    reviewed_unchanged = 0
    coverage = _window_coverage(db, scope, since, utcnow())
    for g in coverage["never_checked"]:
        sections["unable"].append({"id": f"cov:{g}", "ticker": g, "kind": "filings",
                                   "reason": "Filings for this company have not been checked yet, so FINN cannot evaluate the plan for it."})
    for f in coverage["failing"]:
        sections["unable"].append({"id": f"cov:{f['ticker']}", "ticker": f["ticker"], "kind": "filings", "last_success": f["last_success"],
                                   "reason": f"The latest filings check failed ({f['error']}). Filings after {f['last_success'][:16].replace('T', ' ')} UTC "
                                             "are unknown — this is not the same as no change."})
    anns = [a for a in db.scalars(select(Announcement).where(Announcement.company_id.in_([c.id for c in scope]),
                                                             Announcement.published_at >= since,
                                                             Announcement.processing_status == "processed")).all() if a.analysis]
    groups: dict[str, list] = {}
    for a in anns:
        groups.setdefault(a.event_group or f"solo{a.id}", []).append(a)

    for key, members in groups.items():
        why = filing_matches(spec, members)
        if not why:
            continue
        members.sort(key=lambda m: m.published_at, reverse=True)
        card = _card(members[0], db, members[1:]).model_dump(mode="json")
        dna = _event_dna(members)
        r = reviews.get(key)
        seen = set(r.filings_seen or []) if r else set()
        version_ids = sorted(m.id for m in members)
        base = {"review_key": key, "version_ids": version_ids, "ticker": members[0].company.ticker, "company": members[0].company.company_name,
                "matched": why, "card": card, "latest_published_at": _iso(members[0].published_at), "kind": "event"}
        ordered = sorted(members, key=lambda m: m.published_at)
        for i, m in enumerate(ordered[:-1]):
            for ms in next_evidence(m.analysis.category, m.analysis.facts or {}, m.published_at.date()):
                later = [x for x in ordered[i + 1:] if resolved_by(ms["kind"], [x.analysis.facts or {}], [x.analysis.category])]
                if later and later[0].id not in seen:
                    step = next(d for d in dna if d["id"] == later[0].id)
                    sections["resolved"].append({**base, "id": f"{key}:res:{ms['kind']}:{later[0].id}", "question": ms["label"],
                                                 "asked_at": _iso(m.published_at), "resolved_by": step})
        for ms in _milestones(members):
            sections["unresolved"].append({**base, "id": f"{key}:open:{ms['kind']}", "question": ms["label"],
                                           "expected_by": ms.get("expected_by"), "basis": ms["basis"]})
        item = {**base, "id": f"{key}:{_version(version_ids)}", "event_dna": dna}
        if r is None:
            sections["new"].append({**item, "status": "new"})
            continue
        unseen = [d for d in dna if d["id"] not in seen]
        if not unseen:
            reviewed_unchanged += 1
            continue
        meaningful = [d for d in unseen if d["role"] not in REPEAT_ROLES]
        if meaningful:
            sections["updated"].append({**item, "status": "updated", "reviewed_at": _iso(r.reviewed_at), "new_filings": meaningful,
                                        "repeats": [d for d in unseen if d["role"] in REPEAT_ROLES]})
        else:
            repeats_suppressed.append({**item, "reviewed_at": _iso(r.reviewed_at), "new_filings": unseen,
                                       "note": "A repeat of a disclosure you already reviewed was filed; it adds nothing new."})

    if any(c.kind == "guidance_cut" for c in spec.conditions):
        for co in scope:
            for gc in guidance_cuts(db, co, since):
                key = f"guid:{gc['guidance_id']}"
                if key in reviews:
                    reviewed_unchanged += 1
                    continue
                sections["new"].append({"id": key, "review_key": key, "version_ids": [gc["guidance_id"]], "kind": "guidance_cut",
                                        "ticker": co.ticker, "company": co.company_name, "status": "new",
                                        "matched": [describe(PlanCondition(kind="guidance_cut"))], "guidance_cut": gc,
                                        "evidence": _guidance_evidence(db, gc), "latest_published_at": gc["date"]})
    mconds = [c for c in spec.conditions if c.kind in ("price_move", "volume")]
    if mconds:
        for co in scope:
            s = snapshot(db, co.id)
            if s is not None and s["behind_schedule"]:
                sections["unable"].append({"id": f"mkt:{co.ticker}", "ticker": co.ticker, "kind": "market",
                                           "reason": f"The latest stored bar ({s['trading_date']}) is several sessions old, so the price conditions "
                                                     "cannot be evaluated for recent sessions."})
                continue
            m = market_check(spec, s)
            for u in m["unable"]:
                sections["unable"].append({"id": f"mkt:{co.ticker}:{u['kind']}", "ticker": co.ticker, "kind": "market",
                                           "reason": f"Unable to evaluate “{u['condition']}”"
                                                     f"{' for the ' + s['trading_date'] + ' session' if s else ''}: {u['reason']}."})
            key = f"mkt:{co.ticker}:{'+'.join(sorted(m['values']))}:{s['trading_date']}" if s else ""
            if m["result"] == "match" and key not in reviews:
                kind = "price_move" if "price_move" in m["values"] else "volume"
                sections["new"].append({"id": key, "review_key": key, "version_ids": [], "kind": "market", "ticker": co.ticker,
                                        "company": co.company_name, "status": "new", "matched": m["matched"],
                                        "market": {"session": s["trading_date"], "value": m["values"][kind], "unit": "%" if kind == "price_move" else "×",
                                                   "values": m["values"], "close": s["close"], "previous_close": s["previous_close"], "source": s["source"]},
                                        "latest_published_at": s["trading_date"]})
    # Saved investigations whose evidence changed since the investor last looked (Competing Hypotheses → Handover).
    from app.models import Investigation

    for inv in db.scalars(select(Investigation).where(Investigation.company_id.in_([c.id for c in scope]))).all():
        if inv.current_version > inv.reviewed_version:
            sections["updated"].append({"id": f"inv:{inv.id}:v{inv.current_version}", "kind": "investigation", "ticker": inv.company.ticker,
                                        "company": inv.company.company_name, "status": "updated", "investigation_id": inv.id,
                                        "question": inv.question, "matched": ["new evidence for a saved investigation"],
                                        "versions": {"reviewed": inv.reviewed_version, "current": inv.current_version},
                                        "latest_published_at": _iso(inv.updated_at)})
    for k in sections:
        seen_ids, uniq = set(), []
        for it in sections[k]:
            if it["id"] not in seen_ids:
                seen_ids.add(it["id"])
                uniq.append(it)
        sections[k] = sorted(uniq, key=lambda x: x.get("latest_published_at") or "", reverse=True)
    fingerprint = hashlib.sha1(json.dumps({k: [i["id"] for i in v] for k, v in sections.items()}, sort_keys=True).encode()).hexdigest()[:16]
    return {"plan_id": plan.id, "plan_version": plan.version, "evaluated_at": _iso(utcnow()), "since": _iso(since),
            "scope": [c.ticker for c in scope], "sections": sections, "repeats_suppressed": repeats_suppressed,
            "reviewed_unchanged": reviewed_unchanged, "coverage": coverage, "fingerprint": fingerprint, "schedule": schedule_text()}


def market_check(spec: PlanSpec, snap: dict | None) -> dict:
    """The production market-rule evaluator, shared by live monitoring and historical replay.
    result: match / no_match / unable. AND needs every condition evaluated and true; a condition that is evaluable and
    false settles AND as no_match even if another is unavailable. OR matches on any true condition."""
    conds = [c for c in spec.conditions if c.kind in ("price_move", "volume")]
    values, hits, unable = {}, [], []
    for c in conds:
        if snap is None:
            unable.append({"kind": c.kind, "condition": describe(c), "reason": "no end-of-day price data"})
            continue
        f = snap["return_pct"] if c.kind == "price_move" else snap["volume_ratio"]
        v = f["value"]
        if v is None:
            unable.append({"kind": c.kind, "condition": describe(c), "reason": f["note"] or "value unavailable"})
            continue
        values[c.kind] = v
        hit = (v >= c.threshold if c.kind == "volume" else
               v <= -c.threshold if c.direction == "down" else v >= c.threshold if c.direction == "up" else abs(v) >= c.threshold)
        hits.append((c, hit))
    if spec.logical_operator == "AND" and len(conds) > 1:
        if any(not h for _, h in hits):
            result = "no_match"
        elif unable:
            result = "unable"
        else:
            result = "match"
        matched = [describe(c) for c, _ in hits] if result == "match" else []
    else:
        matched = [describe(c) for c, h in hits if h]
        result = "match" if matched else ("unable" if unable and not hits else "no_match")
    return {"result": result, "matched": matched, "values": {k: round(v, 2) for k, v in values.items()}, "unable": unable}


def filing_matches(spec: PlanSpec, members) -> list[str]:
    """Which plan conditions a group of filings satisfies (shared by live monitoring and replay)."""
    why = set()
    for c in [c for c in spec.conditions if c.kind == "filing"]:
        for m in members:
            ea = m.analysis
            if ea is None or ea.category not in (c.categories or []):
                continue
            f = ea.facts or {}
            if c.roles and not _role_match(f, c.roles):
                continue
            if c.rating_action and f.get("action") != c.rating_action:
                continue
            if c.change and _change_of(f.get("action")) != c.change:
                continue
            why.add(describe(c))
            break
    if any(c.kind == "fundraise_update" for c in spec.conditions) and \
            any(m.analysis and ((m.analysis.facts or {}).get("financial_event") or {}).get("action") == "issuance" for m in members):
        why.add(describe(PlanCondition(kind="fundraise_update")))
    return sorted(why)


def _change_of(action: str | None) -> str | None:
    return ("departure" if action in ("departure", "cessation", "retirement", "resignation")
            else "appointment" if action in ("appointment", "reappointment") else None)


def _role_match(facts: dict, roles: list[str]) -> bool:
    from app.services.alerts import ROLE_GROUPS

    return facts.get("role") in set().union(*(ROLE_GROUPS[r] for r in roles))


def record(db: Session, plan, result: dict) -> bool:
    """Persist an evaluation once per (plan, version, fingerprint): refreshes or concurrent runs that see the same items
    add nothing. Returns True when this evaluation changed what the briefing contains."""
    from sqlalchemy.exc import IntegrityError

    from app.models import PlanEvaluation

    row = PlanEvaluation(plan_id=plan.id, plan_version=plan.version, fingerprint=result["fingerprint"], evaluated_at=utcnow(),
                         summary={k: len(v) for k, v in result["sections"].items()} | {"repeats_suppressed": len(result["repeats_suppressed"])},
                         item_ids={k: [i["id"] for i in v] for k, v in result["sections"].items()})
    try:
        with db.begin_nested():
            db.add(row)
        created = True
    except IntegrityError:
        created = False
    plan.last_evaluated_at = utcnow()
    db.commit()
    return created


def review_item(db: Session, key: str, version_ids: list[int], action: str, ticker: str) -> dict:
    """Explicit review of a briefing item that is not an exchange-filing event (guidance revision, market session).
    Stable key + version; repeating the request is harmless."""
    from sqlalchemy.exc import IntegrityError

    from app.models import Company, EventReview

    if not re.match(r"^(guid:\d+|mkt:[A-Z0-9&-]+:(price_move|volume):\d{4}-\d{2}-\d{2})$", key):
        raise ValueError("unknown briefing item")
    co = db.scalars(select(Company).where(Company.ticker == ticker)).first()
    if co is None:
        raise ValueError("unknown company")
    r = db.scalars(select(EventReview).where(EventReview.event_key == key)).first()
    if action == "reopen":
        if r is not None:
            db.delete(r)
            db.commit()
        return {"review_key": key, "status": "new"}
    if r is None:
        try:
            with db.begin_nested():
                r = EventReview(event_key=key, company_id=co.id, filings_seen=sorted(set(version_ids)), status="reviewed")
                db.add(r)
        except IntegrityError:   # a concurrent request created it first
            r = db.scalars(select(EventReview).where(EventReview.event_key == key)).first()
    r.filings_seen = sorted(set(r.filings_seen or []) | set(version_ids))
    r.reviewed_at = r.updated_at = utcnow()
    db.commit()
    return {"review_key": key, "status": r.status, "filings_seen": r.filings_seen}


def evaluate_enabled(db: Session) -> dict:
    from app.models import MonitoringPlan

    out = {"evaluated": 0, "changed": 0}
    for plan in db.scalars(select(MonitoringPlan).where(MonitoringPlan.enabled.is_(True))).all():
        out["evaluated"] += 1
        out["changed"] += int(record(db, plan, evaluate(db, plan)))
    return out
