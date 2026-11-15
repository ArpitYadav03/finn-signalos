"""Alerts assistant: an agent that turns requests like "turn off the Reliance alert and warn me if TCS drops 3%"
into concrete, validated actions.

* Read tools run immediately (rules, alerts, preferences, companies, market snapshot).
* Write tools only *propose* typed actions (services/alert_ops.py). Each proposal is validated on the spot;
  validation errors go back to the model so it can fix them. Nothing changes until the user taps Apply.
* Fast-tier model (a structured, low-reasoning task). Without a key, a rule-based planner covers the common
  commands (create / enable / disable / delete / thresholds / list).
"""
from __future__ import annotations

import logging
import re

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import ToolSpec, run_agent
from app.llm.client import get_llm, tracked_run
from app.llm.prompts import ALERT_AGENT_PROMPT_VERSION, ALERT_AGENT_SYSTEM
from app.models import Alert, AlertRule, Company, UserPreference
from app.services import alert_ops
from app.services import alerts as alert_svc
from app.services import market as market_svc

log = logging.getLogger(__name__)


class _Ctx:
    def __init__(self, db: Session):
        self.db = db
        self.proposals: list = []

    # ---- read tools
    def list_rules(self) -> dict:
        rules = self.db.scalars(select(AlertRule).order_by(AlertRule.id)).all()
        return {"rules": [{"rule_id": r.id, "enabled": r.enabled, "ticker": r.company.ticker if r.company else None,
                           "description": alert_svc.describe(alert_svc.AlertRuleSpec.model_validate(r.parameters)),
                           "spec": r.parameters} for r in rules]}

    def list_recent_alerts(self, limit: int = 10) -> dict:
        rows = self.db.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(min(limit, 30))).all()
        return {"alerts": [{"ticker": a.company.ticker, "message": a.message, "as_of": str(a.as_of), "status": a.status,
                            "rule_id": a.rule_id} for a in rows]}

    def get_preferences(self) -> dict:
        prefs = {p.key: p.value for p in self.db.scalars(select(UserPreference)).all()}
        return {"thresholds": prefs.get("thresholds"), "watched_categories": prefs.get("watched_categories", {}).get("categories")}

    def list_companies(self) -> dict:
        return {"companies": [{"ticker": c.ticker, "name": c.company_name} for c in self.db.scalars(select(Company)).all()]}

    def get_market_snapshot(self, ticker: str) -> dict:
        c = self.db.scalars(select(Company).where(Company.ticker == ticker.upper())).first()
        if not c:
            return {"error": f"Unknown ticker {ticker}"}
        s = market_svc.snapshot(self.db, c.id)
        return s and {k: s[k] for k in ("trading_date", "close", "return_pct", "volume_ratio")} or {"error": "no price data"}

    # ---- propose tools
    def _propose(self, raw: dict) -> dict:
        try:
            action = alert_ops.ActionList.validate_python([raw])[0]
            alert_ops.validate_action(self.db, action)
        except (ValidationError, alert_ops.OpError, ValueError) as e:
            return {"error": str(e)[:400]}
        self.proposals.append(action)
        return {"ok": True, "proposed": alert_ops.describe_action(self.db, action)}

    def propose_create_rule(self, ticker: str | None = None, conditions: list | None = None, logical_operator: str = "OR",
                            min_importance: str = "medium", name: str | None = None) -> dict:
        return self._propose({"type": "create_rule", "name": name, "spec": {
            "ticker": ticker, "conditions": conditions or [], "logical_operator": logical_operator, "min_importance": min_importance}})

    def propose_update_rule(self, rule_id: int, enabled: bool | None = None) -> dict:
        return self._propose({"type": "update_rule", "rule_id": rule_id, "enabled": enabled})

    def propose_delete_rule(self, rule_id: int) -> dict:
        return self._propose({"type": "delete_rule", "rule_id": rule_id})

    def propose_set_thresholds(self, price_move_pct: float | None = None, volume_multiple: float | None = None) -> dict:
        return self._propose({"type": "set_thresholds", "price_move_pct": price_move_pct, "volume_multiple": volume_multiple})

    def propose_set_watched_categories(self, categories: list[str]) -> dict:
        return self._propose({"type": "set_watched_categories", "categories": categories})


def _tools(ctx: _Ctx) -> list[ToolSpec]:
    obj = {"type": "object", "properties": {}}
    cond = {"type": "object", "properties": {
        "metric": {"type": "string", "enum": ["daily_return_pct", "abs_daily_return_pct", "volume_ratio", "announcement_category"]},
        "operator": {"type": "string", "enum": ["gt", "lt", "gte", "lte", "in"]},
        "value": {"description": "number, or list of category strings for announcement_category"},
        "roles": {"type": "array", "items": {"type": "string", "enum": ["CEO", "CFO", "MD", "Chairperson", "COO", "Executive Director"]},
                  "description": "only for management_change: restrict to these roles when the user names them"},
        "rating_action": {"type": "string", "enum": ["downgrade", "upgrade"],
                          "description": "only for credit_rating: when the user asks for downgrades or upgrades specifically"}},
        "required": ["metric", "operator", "value"]}
    return [
        ToolSpec("list_rules", "Current alert rules with ids.", obj, ctx.list_rules),
        ToolSpec("list_recent_alerts", "Recently triggered alerts.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, ctx.list_recent_alerts),
        ToolSpec("get_preferences", "Default thresholds and followed filing categories.", obj, ctx.get_preferences),
        ToolSpec("list_companies", "Known companies (ticker + name) for resolving names like 'Reliance'.", obj, ctx.list_companies),
        ToolSpec("get_market_snapshot", "Latest EOD close, day return and volume ratio for a ticker.",
                 {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}, ctx.get_market_snapshot),
        ToolSpec("propose_create_rule", "Propose a new alert rule.",
                 {"type": "object", "properties": {"ticker": {"type": "string", "description": "NSE symbol; omit for the whole portfolio"}, "conditions": {"type": "array", "items": cond},
                                                   "logical_operator": {"type": "string", "enum": ["AND", "OR"]},
                                                   "min_importance": {"type": "string", "enum": ["routine", "low", "medium", "high"]},
                                                   "name": {"type": "string"}}, "required": ["conditions"]}, ctx.propose_create_rule),
        ToolSpec("propose_update_rule", "Propose enabling/disabling an existing rule.",
                 {"type": "object", "properties": {"rule_id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["rule_id", "enabled"]},
                 ctx.propose_update_rule),
        ToolSpec("propose_delete_rule", "Propose deleting a rule.",
                 {"type": "object", "properties": {"rule_id": {"type": "integer"}}, "required": ["rule_id"]}, ctx.propose_delete_rule),
        ToolSpec("propose_set_thresholds", "Propose new default thresholds for unusual moves.",
                 {"type": "object", "properties": {"price_move_pct": {"type": "number"}, "volume_multiple": {"type": "number"}}}, ctx.propose_set_thresholds),
        ToolSpec("propose_set_watched_categories", "Propose the full list of filing categories to follow.",
                 {"type": "object", "properties": {"categories": {"type": "array", "items": {"type": "string"}}}, "required": ["categories"]},
                 ctx.propose_set_watched_categories),
    ]


# ------------------------------------------------------------------ deterministic planner


def _rules_for(ctx: _Ctx, text: str) -> list[AlertRule]:
    rules = ctx.db.scalars(select(AlertRule).order_by(AlertRule.id)).all()
    if re.search(r"\ball\b|every", text):
        return list(rules)
    hits = []
    for r in rules:
        names = []
        if r.company:
            names = [r.company.ticker.lower(), r.company.company_name.lower().split()[0]]
        elif not r.company_id:
            names = ["portfolio"]
        if any(n and len(n) > 2 and re.search(rf"\b{re.escape(n)}\b", text) for n in names):
            hits.append(r)
    m = re.search(r"#?\brule\s*#?(\d+)", text)
    if m:
        hits += [r for r in rules if r.id == int(m.group(1))]
    return list(dict.fromkeys(hits))


VERBS = r"(?:also )?(?:turn|disable|enable|pause|resume|mute|unmute|delete|remove|set|raise|lower|change|alert|notify|warn|tell|create|add|show|list)"


def _plan_without_llm(ctx: _Ctx, text: str) -> str:
    """Split compound requests ('pause X and set threshold to 5%') into clauses and plan each one."""
    clauses = [c for c in re.split(rf"\s*(?:;|,\s*(?={VERBS})|\bthen\b|\band\s+(?={VERBS}))\s*", text, flags=re.I) if c and c.strip()]
    replies = [_plan_single(ctx, c) for c in (clauses or [text])]
    return " ".join(replies)


def _plan_single(ctx: _Ctx, text: str) -> str:
    t = text.lower()
    if re.search(r"\b(delete|remove)\b", t):
        rules = _rules_for(ctx, t)
        for r in rules:
            ctx._propose({"type": "delete_rule", "rule_id": r.id})
        return f"Found {len(rules)} matching rule(s) to delete." if rules else "I couldn't tell which rule to delete — name the stock or say 'rule #id'."
    for words, enabled in ((r"\b(disable|turn off|pause|stop|mute|switch off)\b", False), (r"\b(enable|turn on|resume|unmute|switch on|reactivate)\b", True)):
        if re.search(words, t):
            rules = [r for r in _rules_for(ctx, t) if r.enabled != enabled]
            for r in rules:
                ctx._propose({"type": "update_rule", "rule_id": r.id, "enabled": enabled})
            return f"{len(rules)} rule(s) to {'enable' if enabled else 'disable'}." if rules else "No matching rule needs that change."
    if re.search(r"threshold|default|by default|generally", t):
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
        mult = re.search(r"(\d+(?:\.\d+)?)\s*(x|×|times)", t)
        if pct or mult:
            ctx._propose({"type": "set_thresholds", "price_move_pct": float(pct.group(1)) if pct else None,
                          "volume_multiple": float(mult.group(1)) if mult else None})
            return "Proposed new default thresholds."
    if re.search(r"\b(show|list|what|which|any)\b.*\b(alerts?|rules?|fired|triggered)\b", t) and not re.search(r"\d\s*%|\bx\b|times", t):
        rules = ctx.list_rules()["rules"]
        alerts = ctx.list_recent_alerts(5)["alerts"]
        r_txt = "; ".join(f"#{r['rule_id']} {r['description']}{'' if r['enabled'] else ' (off)'}" for r in rules) or "no custom rules"
        a_txt = "; ".join(a["message"] for a in alerts) or "no alerts yet"
        return f"Rules: {r_txt}. Recent alerts: {a_txt}."
    parsed = alert_svc.parse_rule_text(text, list(ctx.db.scalars(select(Company)).all()))
    if parsed.spec is None:
        return parsed.explanation if parsed.unsupported else \
            "I couldn't map that to a rule. Try “TCS falls more than 3%”, “pause the Reliance alert” or “any CFO change”."
    res = ctx._propose({"type": "create_rule", "spec": parsed.spec.model_dump()})
    if "ok" not in res:
        return f"Can't create that rule: {res['error']}"
    return "Here's how I read it." + (f" Not included (not supported): {'; '.join(parsed.unsupported)}." if parsed.unsupported else "")


def assist(db: Session, message: str, history: list[dict] | None = None) -> dict:
    ctx = _Ctx(db)
    llm = get_llm()
    mode, steps, reply = "rules-v1", [], None
    with tracked_run(db, "alerts_assistant") as tracker:
        if llm:
            try:
                msgs = [{"role": m["role"], "content": m["content"][:800]} for m in (history or [])[-6:]
                        if m.get("role") in ("user", "assistant")]
                msgs.append({"role": "user", "content": message})
                res = run_agent(llm, tracker, "fast", ALERT_AGENT_SYSTEM, msgs, _tools(ctx), max_steps=8)
                reply, steps = res.text, res.steps
                mode = f"llm:{tracker.last_model or llm.model_for('fast')}"
            except Exception as e:
                log.warning("Alerts agent LLM failed, using rules: %s", e)
                ctx.proposals.clear()
        if reply is None:
            reply = _plan_without_llm(ctx, message)
    from app.ingestion.pipeline import portfolio_companies

    holdings = len(portfolio_companies(db))
    for a in ctx.proposals:   # deterministic guard on model proposals: the user's own filters always survive
        if getattr(a, "spec", None) is not None:
            a.spec = alert_svc.tighten(a.spec, message)
    # Equivalent proposals collapse into one; a proposal identical to an existing rule is flagged, not silently added.
    from app.models import AlertRule as _Rule

    existing = {}
    for r in db.scalars(select(_Rule)).all():
        try:
            existing[alert_svc.signature(alert_svc.AlertRuleSpec.model_validate(r.parameters))] = r
        except Exception:   # noqa: BLE001 — a malformed stored rule must not break the assistant
            continue
    uniq, seen_sigs, dupes = [], set(), {}
    for a in ctx.proposals:
        spec = getattr(a, "spec", None)
        if spec is None or a.type != "create_rule":
            uniq.append(a)
            continue
        sig = alert_svc.signature(spec)
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        if sig in existing:
            dupes[id(a)] = existing[sig]
        uniq.append(a)
    ctx.proposals = uniq

    def view(a) -> dict:
        out = {**a.model_dump(), "description": alert_ops.describe_action(db, a)}
        if id(a) in dupes:
            r = dupes[id(a)]
            out["duplicate_of"] = {"id": r.id, "sentence": alert_svc.describe_human(alert_svc.AlertRuleSpec.model_validate(r.parameters),
                                                                                     r.company.company_name if r.company else None)}
        spec = getattr(a, "spec", None)
        if spec is not None:
            name = alert_ops._company_name(db, spec.ticker)
            out["preview"] = alert_svc.preview(spec, name, holdings)
        return out

    return {
        "reply": reply, "mode": mode, "prompt_version": ALERT_AGENT_PROMPT_VERSION, "steps": steps,
        "actions": [view(a) for a in ctx.proposals],
        "warnings": alert_svc.unsupported_in(message),  # parts of the request FINN can't watch, shown before saving
    }
