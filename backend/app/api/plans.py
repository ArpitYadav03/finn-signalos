"""Investor Handover API: describe → review plan → save → monitor → return briefing → review."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.ingestion.pipeline import portfolio_companies
from app.models import Company, MonitoringPlan, MonitoringPlanVersion, PlanEvaluation
from app.services import monitoring as M

router = APIRouter(prefix="/api/v1")


class DraftIn(BaseModel):
    text: str = Field(min_length=3, max_length=600)
    clarification: float | None = None


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=600)
    spec: dict


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _out(db: Session, p: MonitoringPlan) -> dict:
    spec = M.PlanSpec.model_validate(p.spec)
    last = db.scalars(select(PlanEvaluation).where(PlanEvaluation.plan_id == p.id).order_by(PlanEvaluation.evaluated_at.desc())).first()
    return {"id": p.id, "name": p.name, "description": p.description, "spec": p.spec, "version": p.version, "enabled": p.enabled,
            "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at), "last_evaluated_at": _iso(p.last_evaluated_at),
            "last_change": {"at": _iso(last.evaluated_at), "summary": last.summary} if last else None,
            "preview": M.preview(spec, len(portfolio_companies(db)))}


def _get(db: Session, plan_id: int) -> MonitoringPlan:
    p = db.get(MonitoringPlan, plan_id)
    if p is None:
        raise HTTPException(404, "Plan not found")
    return p


def _validate(spec: dict, db: Session) -> M.PlanSpec:
    try:
        s = M.PlanSpec.model_validate(spec)
    except ValidationError as e:
        raise HTTPException(422, "; ".join(x["msg"] for x in e.errors())[:400]) from None
    if s.tickers:
        known = set(db.scalars(select(Company.ticker).where(Company.ticker.in_(s.tickers))).all())
        missing = sorted(set(s.tickers) - known)
        if missing:
            raise HTTPException(422, f"FINN doesn't track {', '.join(missing)} yet — research it once to add it.")
    return s


@router.post("/plans/draft")
def draft(body: DraftIn, db: Session = Depends(get_db)):
    """Plain words → a plan to review. Nothing is saved."""
    companies = db.scalars(select(Company)).all()
    r = M.parse_plan(body.text, companies)
    if r["clarification"] and body.clarification is not None:
        r.update(M.apply_clarification(r["spec"], body.text, body.clarification))
    spec = r["spec"]
    return {"spec": spec.model_dump() if spec else None, "unsupported": r["unsupported"], "clarification": r["clarification"],
            "explanation": r["explanation"], "preview": M.preview(spec, len(portfolio_companies(db))) if spec else None}


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    return [_out(db, p) for p in db.scalars(select(MonitoringPlan).order_by(MonitoringPlan.created_at)).all()]


@router.post("/plans", status_code=201)
def create_plan(body: PlanIn, db: Session = Depends(get_db)):
    spec = _validate(body.spec, db)
    dup = next((p for p in db.scalars(select(MonitoringPlan)).all() if M.canonical(M.PlanSpec.model_validate(p.spec)) == M.canonical(spec)), None)
    if dup:   # duplicate submit / same plan twice: return the existing plan instead of creating a second one
        return {**_out(db, dup), "duplicate_of": dup.id}
    p = MonitoringPlan(name=body.name.strip(), description=body.description.strip(), spec=spec.model_dump(), version=1, enabled=True)
    db.add(p)
    db.flush()
    db.add(MonitoringPlanVersion(plan_id=p.id, version=1, spec=p.spec))
    db.commit()
    M.record(db, p, M.evaluate(db, p))
    return _out(db, p)


@router.put("/plans/{plan_id:int}")
def update_plan(plan_id: int, body: PlanIn, db: Session = Depends(get_db)):
    p = _get(db, plan_id)
    spec = _validate(body.spec, db)
    p.name, p.description = body.name.strip(), body.description.strip()
    if spec.model_dump() != p.spec:
        p.version += 1
        p.spec = spec.model_dump()
        db.add(MonitoringPlanVersion(plan_id=p.id, version=p.version, spec=p.spec))
    p.updated_at = utcnow()
    db.commit()
    return _out(db, p)


@router.post("/plans/{plan_id:int}/pause")
def pause(plan_id: int, db: Session = Depends(get_db)):
    return _toggle(db, plan_id, "pause")


@router.post("/plans/{plan_id:int}/resume")
def resume(plan_id: int, db: Session = Depends(get_db)):
    return _toggle(db, plan_id, "resume")


def _toggle(db: Session, plan_id: int, action: str):
    p = _get(db, plan_id)
    p.enabled = action == "resume"
    p.updated_at = utcnow()
    db.commit()
    return _out(db, p)


@router.delete("/plans/{plan_id:int}", status_code=204)
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    p = db.get(MonitoringPlan, plan_id)
    if p is not None:
        db.delete(p)
        db.commit()


@router.get("/plans/{plan_id:int}/versions")
def versions(plan_id: int, db: Session = Depends(get_db)):
    return [{"version": v.version, "spec": v.spec, "created_at": _iso(v.created_at)}
            for v in db.scalars(select(MonitoringPlanVersion).where(MonitoringPlanVersion.plan_id == plan_id).order_by(MonitoringPlanVersion.version)).all()]


@router.get("/plans/{plan_id:int}/briefing")
def briefing(plan_id: int, db: Session = Depends(get_db)):
    """Changes since your last review for this plan. Read-only: opening it marks nothing reviewed."""
    p = _get(db, plan_id)
    out = M.evaluate(db, p)
    last = db.scalars(select(PlanEvaluation).where(PlanEvaluation.plan_id == p.id).order_by(PlanEvaluation.evaluated_at.desc())).first()
    return {**out, "plan": _out(db, p), "last_recorded": {"at": _iso(last.evaluated_at), "fingerprint": last.fingerprint} if last else None,
            "paused": not p.enabled}


class ReviewIn(BaseModel):
    review_key: str
    ticker: str
    version_ids: list[int] = []
    action: str = "reviewed"


@router.post("/plans/items/review")
def review(body: ReviewIn, db: Session = Depends(get_db)):
    """Explicit review of a briefing item by stable key + version. Filing events reuse the event review record so the
    Briefing feed and every plan agree."""
    from app.api.routes import review_action

    if body.action not in ("reviewed", "reopen"):
        raise HTTPException(422, "action must be reviewed or reopen")
    if body.review_key.startswith(("guid:", "mkt:")):
        try:
            return M.review_item(db, body.review_key, body.version_ids, body.action, body.ticker)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    return review_action(body.review_key, {"action": body.action, "filings": body.version_ids}, db)


# ------------------------------------------------------------------ Historical Alert Replay

class ReplayIn(BaseModel):
    start: str
    end: str
    mode: str = "public"
    version: int | None = None


def _dates(start: str, end: str, db: Session):
    from datetime import date

    from sqlalchemy import func

    from app.models import Price

    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD") from None
    last = db.scalar(select(func.max(Price.trading_date)))
    if s > e:
        raise HTTPException(422, "The start date must be on or before the end date.")
    if last and e > last:
        raise HTTPException(422, f"FINN has data only up to {last.isoformat()}; a replay cannot look past the latest stored session.")
    if (e - s).days > 180:
        raise HTTPException(422, "Replay at most 180 days at a time.")
    return s, e


def _spec_for(db: Session, p: MonitoringPlan, version: int | None) -> tuple[int, dict]:
    if version is None or version == p.version:
        return p.version, p.spec
    v = db.scalars(select(MonitoringPlanVersion).where(MonitoringPlanVersion.plan_id == p.id, MonitoringPlanVersion.version == version)).first()
    if v is None:
        raise HTTPException(404, "Plan version not found")
    return v.version, v.spec


@router.get("/plans/{plan_id:int}/replay/coverage")
def replay_coverage(plan_id: int, start: str, end: str, version: int | None = None, db: Session = Depends(get_db)):
    """Step 3: what the data can support for this plan and date range — before anything runs."""
    from app.services import replay as R

    p = _get(db, plan_id)
    s, e = _dates(start, end, db)
    ver, spec = _spec_for(db, p, version)
    return {"plan_id": p.id, "plan_version": ver, **R.coverage(db, M.PlanSpec.model_validate(spec), s, e)}


def _run_out(run, *, full: bool = True) -> dict:
    from app.services import replay as R

    base = {"id": run.id, "plan_id": run.plan_id, "plan_version": run.plan_version, "spec": run.spec,
            "conditions": [M.describe(M.PlanCondition(**c)) for c in run.spec["conditions"]], "logical_operator": run.spec.get("logical_operator", "OR"),
            "start": run.start_date.isoformat(), "end": run.end_date.isoformat(), "mode": run.mode, "status": run.status,
            "progress": run.progress or {}, "error": run.error, "created_at": _iso(run.created_at), "finished_at": _iso(run.finished_at),
            "summary": R.summary(run)}
    return {**base, "coverage": run.coverage, "results": run.results or []} if full else base


@router.post("/plans/{plan_id:int}/replays", status_code=202)
def replay_start(plan_id: int, body: ReplayIn, db: Session = Depends(get_db)):
    """Step 4: run the replay in the background. Separate from live monitoring: no alerts, no change to the plan."""
    from app.models import ReplayRun
    from app.services import replay as R

    if body.mode not in ("public", "indexed"):
        raise HTTPException(422, "mode must be public or indexed")
    p = _get(db, plan_id)
    s, e = _dates(body.start, body.end, db)
    ver, spec = _spec_for(db, p, body.version)
    active = db.scalars(select(ReplayRun).where(ReplayRun.plan_id == p.id, ReplayRun.plan_version == ver, ReplayRun.start_date == s,
                                               ReplayRun.end_date == e, ReplayRun.mode == body.mode,
                                               ReplayRun.status.in_(("queued", "running")))).first()
    if active:   # double submit → the run already in progress
        return {**_run_out(active, full=False), "duplicate_of": active.id}
    cov = R.coverage(db, M.PlanSpec.model_validate(spec), s, e)
    if not cov["session_count"]:
        raise HTTPException(422, "No trading sessions with stored prices in that range.")
    run = ReplayRun(plan_id=p.id, plan_version=ver, spec=spec, start_date=s, end_date=e, mode=body.mode, status="queued", cancel_requested=False,
                    progress={"done": 0, "total": cov["session_count"]}, coverage=cov, results=[])
    db.add(run)
    db.commit()
    R.start(run.id)
    return _run_out(run, full=False)


@router.get("/plans/{plan_id:int}/replays")
def replay_list(plan_id: int, db: Session = Depends(get_db)):
    from app.models import ReplayRun
    from app.services import replay as R

    R.recover_stale(db)
    return [_run_out(r, full=False) for r in db.scalars(select(ReplayRun).where(ReplayRun.plan_id == plan_id).order_by(ReplayRun.created_at.desc())).all()]


def _run(db: Session, run_id: int):
    from app.models import ReplayRun

    r = db.get(ReplayRun, run_id)
    if r is None:
        raise HTTPException(404, "Replay not found")
    return r


@router.get("/replays/{run_id:int}")
def replay_get(run_id: int, db: Session = Depends(get_db)):
    from app.services import replay as R

    R.recover_stale(db)
    return _run_out(_run(db, run_id))


@router.post("/replays/{run_id:int}/cancel")
def replay_cancel(run_id: int, db: Session = Depends(get_db)):
    r = _run(db, run_id)
    if r.status in ("queued", "running"):
        r.cancel_requested = True
        if r.status == "queued":
            r.status, r.finished_at = "cancelled", utcnow()
        db.commit()
    return _run_out(r, full=False)


@router.post("/replays/{run_id:int}/resume", status_code=202)
def replay_resume(run_id: int, db: Session = Depends(get_db)):
    """Recovery: continue an interrupted or cancelled run from the last saved session."""
    from app.services import replay as R

    r = _run(db, run_id)
    if r.status not in ("interrupted", "cancelled", "failed"):
        raise HTTPException(409, f"This replay is {r.status}; only an interrupted, cancelled or failed run can be resumed.")
    r.status, r.cancel_requested, r.error = "interrupted", False, None
    db.commit()
    R.start(r.id)
    return _run_out(r, full=False)
