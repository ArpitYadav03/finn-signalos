"""Durable ingestion jobs with database leases.

* A job row is created once per unit of work (key, e.g. ``doc:123``) and reused on every later request, so repeated
  research questions never repeat finished work.
* ``claim`` is a single conditional UPDATE (compare-and-set). It succeeds for exactly one worker, whether the workers
  are threads in one API process or separate processes sharing the database (SQLite or PostgreSQL).
* A running job holds a lease that is renewed at every stage (``progress``). If its worker dies, the lease expires and
  any worker may claim it again — that is restart recovery; ``attempts`` keeps counting.
* Failures are classified. Transient ones (network, rate limit, 5xx, unknown) are retried with bounded exponential
  backoff; terminal ones (404, not a PDF, too large, no text layer, off allow-list) stop immediately and keep their
  explanation.
"""
from __future__ import annotations

import logging
import os
import random
import socket
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import IngestionJob

log = logging.getLogger(__name__)

LEASE_SECONDS = 120
BACKOFF_BASE = 30          # seconds; doubled per attempt
BACKOFF_CAP = 30 * 60      # never wait more than 30 minutes between attempts
TERMINAL = {"not_found", "forbidden", "access_denied", "not_pdf", "too_large", "no_text"}
EXPLAIN = {
    "network": "The source could not be reached (timeout or connection error). FINN will retry.",
    "rate_limited": "The source asked FINN to slow down (HTTP 429). FINN will retry later.",
    "server_error": "The source returned a server error (5xx). FINN will retry.",
    "not_found": "The source no longer serves this document (HTTP 404).",
    "forbidden": "The document's host is outside FINN's primary-source allow-list, so it is not downloaded.",
    "access_denied": "The host refused automated access (HTTP 401/403). FINN respects that: it does not retry or work around it; open the document on the company's site.",
    "not_pdf": "The link does not return a PDF.",
    "too_large": "The PDF is larger than FINN's download limit.",
    "no_text": "The PDF has no text layer (probably a scanned image); FINN does not OCR documents.",
    "unknown": "Unexpected error while processing the document. FINN will retry a limited number of times.",
}


def worker_id(prefix: str = "w") -> str:
    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class TerminalError(RuntimeError):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def classify_error(exc: BaseException) -> str:
    if isinstance(exc, TerminalError):
        return exc.error_class
    m = str(exc).lower()
    if "not found" in m or "404" in m:
        return "not_found"
    if "allow-list" in m or "only https" in m or "redirected off" in m:
        return "forbidden"
    if "403 forbidden" in m or "401 unauthorized" in m or "'403" in m or "'401" in m:
        return "access_denied"
    if "not a pdf" in m:
        return "not_pdf"
    if "exceeds" in m and "mb" in m:
        return "too_large"
    if "no extractable text" in m:
        return "no_text"
    if "429" in m or "too many requests" in m:
        return "rate_limited"
    if any(x in m for x in (" 500", " 502", " 503", " 504", "server error")):
        return "server_error"
    if any(x in m for x in ("timeout", "timed out", "connection", "network", "temporarily", "reset by peer", "name resolution")):
        return "network"
    return "unknown"


def backoff_seconds(attempts: int, *, jitter: bool = True) -> int:
    base = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** max(0, attempts - 1)))
    return int(base * (random.uniform(0.8, 1.2) if jitter else 1))


def ensure_job(db: Session, kind: str, key: str, *, company_id: int | None = None, document_id: int | None = None,
               max_attempts: int = 4) -> IngestionJob:
    job = db.scalars(select(IngestionJob).where(IngestionJob.key == key)).first()
    if job is None:
        job = IngestionJob(kind=kind, key=key, company_id=company_id, document_id=document_id, max_attempts=max_attempts,
                           status="queued", next_attempt_at=utcnow(), progress={}, result={})
        db.add(job)
        try:
            db.commit()
        except Exception:      # another worker created it first: use theirs
            db.rollback()
            job = db.scalars(select(IngestionJob).where(IngestionJob.key == key)).one()
    return job


def claim(db: Session, job_id: int, owner: str, *, force: bool = False) -> bool:
    """Atomically take the lease. Returns True for exactly one caller."""
    now = utcnow()
    due = and_(IngestionJob.status.in_(("queued", "retry")), IngestionJob.next_attempt_at <= now)
    abandoned = and_(IngestionJob.status == "running", IngestionJob.lease_expires_at < now)
    cond = or_(due, abandoned, IngestionJob.status.in_(("queued", "retry"))) if force else or_(due, abandoned)
    res = db.execute(update(IngestionJob).where(IngestionJob.id == job_id, cond).values(
        status="running", lease_owner=owner, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
        attempts=IngestionJob.attempts + 1, started_at=now, updated_at=now).execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def claim_next(db: Session, owner: str, kinds: tuple[str, ...] = ("index_document",)) -> IngestionJob | None:
    now = utcnow()
    candidates = db.scalars(select(IngestionJob.id).where(
        IngestionJob.kind.in_(kinds),
        or_(and_(IngestionJob.status.in_(("queued", "retry")), IngestionJob.next_attempt_at <= now),
            and_(IngestionJob.status == "running", IngestionJob.lease_expires_at < now)))
        .order_by(IngestionJob.next_attempt_at).limit(10)).all()
    for jid in candidates:
        if claim(db, jid, owner):
            return db.get(IngestionJob, jid)
    return None


def _owned(db: Session, job: IngestionJob, owner: str) -> bool:
    db.refresh(job)
    return job.status == "running" and job.lease_owner == owner


def progress(db: Session, job: IngestionJob, owner: str, stage: str, **info) -> None:
    """Record the current stage and renew the lease (heartbeat). Raises if the lease was lost."""
    now = utcnow()
    p = dict(job.progress or {})
    done = list(p.get("stages_done", []))
    if p.get("stage") and p["stage"] != stage and p["stage"] not in done:
        done.append(p["stage"])
    p.update({"stage": stage, "stages_done": done, "updated_at": now.isoformat() + "Z", **info})
    res = db.execute(update(IngestionJob).where(IngestionJob.id == job.id, IngestionJob.lease_owner == owner,
                                                IngestionJob.status == "running")
                     .values(progress=p, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), updated_at=now)
                     .execution_options(synchronize_session=False))
    db.commit()
    if res.rowcount != 1:
        raise RuntimeError(f"Lease lost on job {job.id}")
    job.progress = p


def succeed(db: Session, job: IngestionJob, owner: str, result: dict | None = None) -> None:
    now = utcnow()
    p = dict(job.progress or {})
    if p.get("stage") and p["stage"] not in p.get("stages_done", []):
        p["stages_done"] = [*p.get("stages_done", []), p["stage"]]
    p["stage"] = "done"
    db.execute(update(IngestionJob).where(IngestionJob.id == job.id, IngestionJob.lease_owner == owner).values(
        status="succeeded", lease_owner=None, lease_expires_at=None, finished_at=now, updated_at=now, error=None,
        error_class=None, progress=p, result=result or {}).execution_options(synchronize_session=False))
    db.commit()


def fail(db: Session, job: IngestionJob, owner: str, exc: BaseException) -> str:
    """Record a failure. Returns the resulting status: 'retry' (with next_attempt_at) or 'failed' (terminal)."""
    db.rollback()
    db.refresh(job)
    cls = classify_error(exc)
    now = utcnow()
    terminal = cls in TERMINAL or job.attempts >= job.max_attempts
    status = "failed" if terminal else "retry"
    nxt = now if terminal else now + timedelta(seconds=backoff_seconds(job.attempts))
    msg = f"{EXPLAIN.get(cls, EXPLAIN['unknown'])} Detail: {str(exc)[:300]}"
    if terminal and cls not in TERMINAL:
        msg = f"Gave up after {job.attempts} attempts. " + msg
    db.execute(update(IngestionJob).where(IngestionJob.id == job.id, IngestionJob.lease_owner == owner).values(
        status=status, error_class=cls, error=msg, next_attempt_at=nxt, lease_owner=None, lease_expires_at=None,
        updated_at=now, finished_at=now if terminal else None).execution_options(synchronize_session=False))
    db.commit()
    db.refresh(job)
    return status


def requeue(db: Session, job: IngestionJob) -> None:
    """Explicit re-run (e.g. force re-index): reset attempts but keep history in result."""
    db.execute(update(IngestionJob).where(IngestionJob.id == job.id, IngestionJob.status != "running").values(
        status="queued", attempts=0, next_attempt_at=utcnow(), error=None, error_class=None)
        .execution_options(synchronize_session=False))
    db.commit()
    db.refresh(job)


def recover_abandoned(db: Session) -> int:
    """Jobs left 'running' with an expired lease (worker crashed or the process restarted) become retryable now."""
    now = utcnow()
    res = db.execute(update(IngestionJob).where(IngestionJob.status == "running", IngestionJob.lease_expires_at < now)
                     .values(status="retry", lease_owner=None, next_attempt_at=now, updated_at=now,
                             error="Recovered after the worker stopped mid-job (lease expired).", error_class="interrupted")
                     .execution_options(synchronize_session=False))
    db.commit()
    if res.rowcount:
        log.warning("Recovered %d abandoned ingestion job(s)", res.rowcount)
    return res.rowcount


def summary(db: Session, company_id: int | None = None) -> dict:
    q = select(IngestionJob)
    if company_id is not None:
        q = q.where(IngestionJob.company_id == company_id)
    rows = db.scalars(q).all()
    out: dict = {"total": len(rows), "by_status": {}}
    for j in rows:
        out["by_status"][j.status] = out["by_status"].get(j.status, 0) + 1
    return out


def as_dict(j: IngestionJob) -> dict:
    iso = lambda d: d.isoformat() + "Z" if isinstance(d, datetime) else None  # noqa: E731
    return {"id": j.id, "kind": j.kind, "key": j.key, "document_id": j.document_id, "status": j.status, "attempts": j.attempts,
            "max_attempts": j.max_attempts, "next_attempt_at": iso(j.next_attempt_at) if j.status == "retry" else None,
            "error_class": j.error_class, "error": j.error, "progress": j.progress, "lease_owner": j.lease_owner,
            "lease_expires_at": iso(j.lease_expires_at), "updated_at": iso(j.updated_at), "finished_at": iso(j.finished_at)}
