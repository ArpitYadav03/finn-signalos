"""Background worker for durable ingestion jobs. Runs as a thread in the API process (JOBS_WORKER_ENABLED) and/or as a
separate process (`python -m app.cli worker`). Any number of workers may run: the database lease decides who works."""
from __future__ import annotations

import logging
import threading

from app.core.db import SessionLocal
from app.models import Document
from app.services import jobs

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None


def run_once(owner: str) -> dict | None:
    """Claim and run one due (or abandoned) job. Returns a short report, or None if nothing was due."""
    from app.ingestion.pipeline import run_claimed_document_job

    db = SessionLocal()
    try:
        job = jobs.claim_next(db, owner)
        if job is None:
            return None
        doc = db.get(Document, job.document_id) if job.document_id else None
        if doc is None:
            jobs.fail(db, job, owner, jobs.TerminalError("not_found", "Document row no longer exists"))
            return {"job": job.id, "status": "failed"}
        run_claimed_document_job(db, doc, job, owner)
        db.refresh(job)
        return {"job": job.id, "status": job.status, "attempts": job.attempts}
    finally:
        db.close()


def loop(poll_seconds: float = 3.0, owner: str | None = None) -> None:
    owner = owner or jobs.worker_id("worker")
    db = SessionLocal()
    try:
        jobs.recover_abandoned(db)
    finally:
        db.close()
    while not _stop.is_set():
        try:
            if run_once(owner) is None:
                _stop.wait(poll_seconds)
        except Exception:  # noqa: BLE001 — a bad job must never kill the worker
            log.exception("Job worker iteration failed")
            _stop.wait(poll_seconds)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=loop, name="finn-job-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
