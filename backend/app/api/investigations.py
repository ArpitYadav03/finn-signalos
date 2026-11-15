"""Competing Hypotheses API: choose question → review hypotheses → compare evidence → save → revisit when evidence changes."""
from __future__ import annotations

import hashlib
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import rate_limit
from app.core.db import get_db, utcnow
from app.ingestion.providers import normalise_ticker
from app.models import Company, Investigation, InvestigationVersion
from app.services import hypotheses as H

router = APIRouter(prefix="/api/v1")
_memo: dict[str, tuple[float, dict]] = {}   # compare → save without re-running the model (10 minutes)


class CompareIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    question: str = Field(min_length=8, max_length=400)
    hypotheses: list[dict] = Field(min_length=2, max_length=3)


def _company(db: Session, ticker: str) -> Company:
    c = db.scalars(select(Company).where(Company.ticker == normalise_ticker(ticker))).first()
    if c is None:
        raise HTTPException(404, f"FINN doesn't track {ticker.upper()} yet — open it in Research once to add it.")
    return c


def _hyps(raw: list[dict]) -> list[H.Hypothesis]:
    try:
        hs = [H.Hypothesis(**h) for h in raw]
    except ValidationError as e:
        raise HTTPException(422, "; ".join(x["msg"] for x in e.errors())[:300]) from None
    if len({h.id for h in hs}) != len(hs) or len({h.text.strip().lower() for h in hs}) != len(hs):
        raise HTTPException(422, "Each hypothesis needs its own id and wording.")
    return hs


def _key(ticker: str, question: str, hs: list[H.Hypothesis]) -> str:
    return hashlib.sha1(json.dumps([ticker, question.strip(), [h.model_dump() for h in hs]]).encode()).hexdigest()


@router.get("/investigations/suggest")
def suggest(ticker: str, question: str, db: Session = Depends(get_db)):
    """Step 2: suggested hypotheses (labelled as FINN's suggestion) or the reason the question can't be investigated."""
    c = _company(db, ticker)
    refusal = H.scope_check(c, question)
    return {"ticker": c.ticker, "company": c.company_name, "refusal": refusal, "hypotheses": [] if refusal else H.SUGGESTED}


@router.post("/investigations/compare", dependencies=[Depends(rate_limit(10, 60))])
def compare(body: CompareIn, db: Session = Depends(get_db)):
    """Step 3: evidence for and against each hypothesis. Nothing is saved."""
    c = _company(db, body.ticker)
    hs = _hyps(body.hypotheses)
    k = _key(c.ticker, body.question, hs)
    hit = _memo.get(k)
    if hit and time.time() - hit[0] < 600:
        res = hit[1]
    else:
        res = H.run(db, c, body.question, hs)
        _memo[k] = (time.time(), res)
    return {"ticker": c.ticker, "company": c.company_name, "question": body.question, "hypotheses": [h.model_dump() for h in hs],
            **H._public(res)}


def _out(db: Session, inv: Investigation, *, full: bool = True) -> dict:
    cur = db.scalars(select(InvestigationVersion).where(InvestigationVersion.investigation_id == inv.id,
                                                        InvestigationVersion.version == inv.current_version)).first()
    base = {"id": inv.id, "ticker": inv.company.ticker, "company": inv.company.company_name, "question": inv.question,
            "hypotheses": inv.hypotheses, "status": inv.status, "current_version": inv.current_version, "reviewed_version": inv.reviewed_version,
            "changed_since_review": inv.current_version > inv.reviewed_version, "created_at": inv.created_at.isoformat() + "Z",
            "updated_at": inv.updated_at.isoformat() + "Z", "last_checked_at": inv.last_checked_at.isoformat() + "Z" if inv.last_checked_at else None,
            "version_created_at": cur.created_at.isoformat() + "Z" if cur else None}
    if not full or cur is None:
        return base
    old = None
    if inv.current_version > inv.reviewed_version:
        r = db.scalars(select(InvestigationVersion).where(InvestigationVersion.investigation_id == inv.id,
                                                          InvestigationVersion.version == inv.reviewed_version)).first()
        old = r.result if r else None
    return {**base, **cur.result, "changes": H.diff(old, cur.result) if old else None}


@router.post("/investigations", status_code=201)
def save(body: CompareIn, db: Session = Depends(get_db)):
    c = _company(db, body.ticker)
    hs = _hyps(body.hypotheses)
    dup = db.scalars(select(Investigation).where(Investigation.company_id == c.id, Investigation.question == body.question.strip())).all()
    for d in dup:   # the same question and hypotheses saved twice (double submit) → the existing investigation
        if [H.Hypothesis(**h) for h in d.hypotheses] == hs:
            return {**_out(db, d), "duplicate_of": d.id}
    hit = _memo.get(_key(c.ticker, body.question, hs))
    res = hit[1] if hit else H.run(db, c, body.question, hs)
    if res["status"] != "ok":
        raise HTTPException(422, res["reason"])
    inv = Investigation(company_id=c.id, question=body.question.strip(), hypotheses=[h.model_dump() for h in hs], status=res["summary"]["state"],
                        current_version=1, reviewed_version=1, judgement_cache={})
    db.add(inv)
    db.flush()
    H.save_version(db, inv, res)
    return _out(db, inv)


@router.get("/investigations")
def list_(ticker: str | None = None, db: Session = Depends(get_db)):
    q = select(Investigation).order_by(Investigation.updated_at.desc())
    if ticker:
        q = q.where(Investigation.company_id == _company(db, ticker).id)
    return [_out(db, i, full=False) for i in db.scalars(q).all()]


def _get(db: Session, inv_id: int) -> Investigation:
    inv = db.get(Investigation, inv_id)
    if inv is None:
        raise HTTPException(404, "Investigation not found")
    return inv


@router.get("/investigations/{inv_id:int}")
def get(inv_id: int, db: Session = Depends(get_db)):
    """Read-only: opening an investigation does not mark its changes as seen."""
    return _out(db, _get(db, inv_id))


class EditIn(BaseModel):
    hypotheses: list[dict] = Field(min_length=2, max_length=3)


@router.put("/investigations/{inv_id:int}", dependencies=[Depends(rate_limit(10, 60))])
def edit(inv_id: int, body: EditIn, db: Session = Depends(get_db)):
    inv = _get(db, inv_id)
    hs = _hyps(body.hypotheses)
    inv.hypotheses = [h.model_dump() for h in hs]
    res = H.run(db, inv.company, inv.question, hs, cache=inv.judgement_cache)
    H.save_version(db, inv, res)
    inv.reviewed_version = inv.current_version      # the investor made this change, so they have seen it
    db.commit()
    return _out(db, inv)


@router.post("/investigations/{inv_id:int}/refresh", dependencies=[Depends(rate_limit(10, 60))])
def refresh(inv_id: int, db: Session = Depends(get_db)):
    inv = _get(db, inv_id)
    changed = H.save_version(db, inv, H.run(db, inv.company, inv.question, [H.Hypothesis(**h) for h in inv.hypotheses], cache=inv.judgement_cache))
    return {**_out(db, inv), "refreshed": True, "evidence_changed": changed}


class SeenIn(BaseModel):
    version: int


@router.post("/investigations/{inv_id:int}/reviewed")
def reviewed(inv_id: int, body: SeenIn, db: Session = Depends(get_db)):
    inv = _get(db, inv_id)
    if not 1 <= body.version <= inv.current_version:
        raise HTTPException(422, "Unknown version")
    inv.reviewed_version = max(inv.reviewed_version, body.version)   # never moves backwards under concurrent clicks
    db.commit()
    return _out(db, inv, full=False)


@router.delete("/investigations/{inv_id:int}", status_code=204)
def delete(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Investigation, inv_id)
    if inv is not None:
        db.delete(inv)
        db.commit()
