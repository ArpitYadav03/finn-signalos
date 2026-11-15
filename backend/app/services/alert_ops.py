"""Write operations on alert rules and preferences, shared by the REST routes and the Alerts assistant.

The assistant only *proposes* these as typed actions; they run when the user applies them.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories import Category
from app.ingestion.providers import normalise_ticker
from app.models import AlertRule, Company, UserPreference
from app.services import alerts as alert_svc
from app.services.alerts import AlertRuleSpec


class OpError(ValueError):
    pass


class CreateRuleAction(BaseModel):
    type: Literal["create_rule"] = "create_rule"
    spec: AlertRuleSpec
    name: str | None = Field(None, max_length=160)


class UpdateRuleAction(BaseModel):
    type: Literal["update_rule"] = "update_rule"
    rule_id: int
    enabled: bool | None = None
    spec: AlertRuleSpec | None = None


class DeleteRuleAction(BaseModel):
    type: Literal["delete_rule"] = "delete_rule"
    rule_id: int


class SetThresholdsAction(BaseModel):
    type: Literal["set_thresholds"] = "set_thresholds"
    price_move_pct: float | None = Field(None, gt=0, le=50)
    volume_multiple: float | None = Field(None, gt=0, le=50)


class SetWatchedCategoriesAction(BaseModel):
    type: Literal["set_watched_categories"] = "set_watched_categories"
    categories: list[str]


Action = Annotated[Union[CreateRuleAction, UpdateRuleAction, DeleteRuleAction, SetThresholdsAction,
                         SetWatchedCategoriesAction], Field(discriminator="type")]
ActionList = TypeAdapter(list[Action])


def _company_name(db: Session, ticker: str | None) -> str | None:
    if not ticker:
        return None
    c = db.scalars(select(Company).where(Company.ticker == ticker.upper())).first()
    return c.company_name if c else ticker


def describe_action(db: Session, a) -> str:
    """Plain-English description shown on the confirmation card."""
    if isinstance(a, CreateRuleAction):
        return "Alert me when " + alert_svc.describe_human(a.spec, _company_name(db, a.spec.ticker))
    if isinstance(a, (UpdateRuleAction, DeleteRuleAction)):
        r = db.get(AlertRule, a.rule_id)
        label = (alert_svc.describe_human(AlertRuleSpec.model_validate(r.parameters), r.company.company_name if r.company else None)
                 if r else f"rule #{a.rule_id}")
        if isinstance(a, DeleteRuleAction):
            return f"Delete the alert “{label}”"
        if a.spec is not None:
            return f"Change it to: alert me when {alert_svc.describe_human(a.spec, _company_name(db, a.spec.ticker))}"
        return f"{'Resume' if a.enabled else 'Pause'} the alert “{label}”"
    if isinstance(a, SetThresholdsAction):
        bits = []
        if a.price_move_pct:
            bits.append(f"a day move beyond ±{a.price_move_pct:g}%")
        if a.volume_multiple:
            bits.append(f"volume above {a.volume_multiple:g}× the usual")
        return "Treat " + " and ".join(bits) + " as unusual"
    from app.domain.categories import CATEGORY_LABELS, Category as Cat

    return "Follow these filing types: " + ", ".join(CATEGORY_LABELS[Cat(c)] for c in a.categories)


def validate_action(db: Session, a) -> None:
    """Raise OpError if the action can't be applied (used before proposing, so the agent can self-correct)."""
    if isinstance(a, CreateRuleAction):
        if a.spec.ticker:
            t = normalise_ticker(a.spec.ticker)
            if not db.scalars(select(Company).where(Company.ticker == t)).first():
                raise OpError(f"Unknown ticker {a.spec.ticker}; use one from list_companies")
            a.spec.ticker = t
        if any(r.parameters == a.spec.model_dump() for r in db.scalars(select(AlertRule)).all()):
            raise OpError("An identical rule already exists")
    elif isinstance(a, (UpdateRuleAction, DeleteRuleAction)):
        if db.get(AlertRule, a.rule_id) is None:
            raise OpError(f"No rule with id {a.rule_id}; call list_rules")
        if isinstance(a, UpdateRuleAction) and a.enabled is None and a.spec is None:
            raise OpError("Nothing to update")
    elif isinstance(a, SetThresholdsAction):
        if a.price_move_pct is None and a.volume_multiple is None:
            raise OpError("Give price_move_pct and/or volume_multiple")
    elif isinstance(a, SetWatchedCategoriesAction):
        bad = [c for c in a.categories if c not in {x.value for x in Category}]
        if bad:
            raise OpError(f"Unknown categories {bad}")


def create_rule(db: Session, spec: AlertRuleSpec, name: str | None = None, text: str | None = None) -> tuple[AlertRule, int]:
    from app.ingestion.pipeline import portfolio_companies

    a = CreateRuleAction(spec=spec, name=name)
    validate_action(db, a)
    company = db.scalars(select(Company).where(Company.ticker == spec.ticker)).first() if spec.ticker else None
    r = AlertRule(name=name or alert_svc.describe(spec), company_id=company.id if company else None,
                  rule_type=spec.rule_type, parameters=spec.model_dump(), enabled=spec.enabled, created_from_text=text)
    db.add(r)
    db.commit()
    fired = alert_svc.evaluate_rule(db, r, portfolio_companies(db) + ([company] if company else []))
    return r, fired


def update_rule(db: Session, rule_id: int, *, enabled: bool | None = None, spec: AlertRuleSpec | None = None,
                name: str | None = None) -> AlertRule:
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise OpError("Rule not found")
    if name is not None:
        r.name = name
    if spec is not None:
        r.parameters, r.rule_type = spec.model_dump(), spec.rule_type
    if enabled is not None:
        r.enabled = enabled
        p = dict(r.parameters)
        p["enabled"] = enabled
        r.parameters = p
    db.commit()
    return r


def delete_rule(db: Session, rule_id: int) -> None:
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise OpError("Rule not found")
    db.delete(r)
    db.commit()


def _upsert_pref(db: Session, key: str, value: dict) -> None:
    row = db.scalars(select(UserPreference).where(UserPreference.key == key)).first()
    if row:
        row.value = value
    else:
        db.add(UserPreference(key=key, value=value))


def set_thresholds(db: Session, price_move_pct: float | None, volume_multiple: float | None) -> None:
    row = db.scalars(select(UserPreference).where(UserPreference.key == "thresholds")).first()
    cur = dict(row.value) if row else {"price_move_pct": 4.0, "volume_multiple": 2.0}
    if price_move_pct:
        cur["price_move_pct"] = price_move_pct
    if volume_multiple:
        cur["volume_multiple"] = volume_multiple
    _upsert_pref(db, "thresholds", cur)
    db.commit()


def set_watched_categories(db: Session, categories: list[str]) -> None:
    validate_action(db, SetWatchedCategoriesAction(categories=categories))
    _upsert_pref(db, "watched_categories", {"categories": categories})
    db.commit()


def apply(db: Session, a) -> dict:
    desc = describe_action(db, a)
    try:
        if isinstance(a, CreateRuleAction):
            r, fired = create_rule(db, a.spec, a.name, "via assistant")
            return {"description": desc, "ok": True, "detail": f"rule #{r.id} created" + (f", {fired} alert(s) fired now" if fired else "")}
        if isinstance(a, UpdateRuleAction):
            update_rule(db, a.rule_id, enabled=a.enabled, spec=a.spec)
        elif isinstance(a, DeleteRuleAction):
            delete_rule(db, a.rule_id)
        elif isinstance(a, SetThresholdsAction):
            set_thresholds(db, a.price_move_pct, a.volume_multiple)
        else:
            set_watched_categories(db, a.categories)
        return {"description": desc, "ok": True, "detail": "done"}
    except (OpError, ValueError) as e:
        db.rollback()
        return {"description": desc, "ok": False, "detail": str(e)}
