"""Durable ingestion jobs: exclusive leases (threads and separate processes), restart recovery, bounded retry,
terminal failures, no repeated downloads. Companies/documents here are SYNTHETIC test rows."""
import multiprocessing as mp
import os
import threading
from datetime import timedelta

import pytest

from app.core.db import SessionLocal, utcnow
from app.ingestion import pipeline
from app.ingestion.http import FetchError
from app.models import Company, Document, IngestionJob
from app.services import jobs


def _doc(db, ticker, n=0):
    co = db.query(Company).filter_by(ticker=ticker).first()
    if co is None:
        co = Company(ticker=ticker, company_name=f"{ticker} (synthetic)")
        db.add(co)
        db.flush()
    d = Document(company_id=co.id, document_type="exchange_filing", title=f"doc {n}", status="discovered",
                 source_url=f"https://www.bseindia.com/{ticker.lower()}-{n}.pdf")
    db.add(d)
    db.commit()
    return d


def test_only_one_thread_can_claim_a_job(db):
    d = _doc(db, "LEASECO")
    job = jobs.ensure_job(db, "index_document", f"doc:{d.id}", company_id=d.company_id, document_id=d.id)
    wins, barrier = [], threading.Barrier(8)

    def contend(i):
        s = SessionLocal()
        try:
            barrier.wait()
            if jobs.claim(s, job.id, f"thread-{i}"):
                wins.append(i)
        finally:
            s.close()

    ts = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1
    db.refresh(job)
    assert job.status == "running" and job.lease_owner == f"thread-{wins[0]}" and job.attempts == 1


def _proc_claim(job_id, owner, q):
    from app.core.db import SessionLocal as SL
    from app.services import jobs as J

    s = SL()
    try:
        q.put((owner, J.claim(s, job_id, owner)))
    finally:
        s.close()


def test_only_one_process_can_claim_a_job(db):
    d = _doc(db, "LEASECO", 1)
    job = jobs.ensure_job(db, "index_document", f"doc:{d.id}", company_id=d.company_id, document_id=d.id)
    ctx = mp.get_context("spawn")     # separate interpreters sharing only the database file (DATABASE_URL in env)
    q = ctx.Queue()
    procs = [ctx.Process(target=_proc_claim, args=(job.id, f"proc-{i}", q)) for i in range(4)]
    [p.start() for p in procs]
    [p.join(60) for p in procs]
    results = [q.get(timeout=5) for _ in procs]
    assert sum(1 for _, ok in results if ok) == 1, results
    db.refresh(job)
    assert job.lease_owner in {o for o, ok in results if ok}


def test_interrupted_job_is_recovered_after_lease_expiry(db):
    d = _doc(db, "LEASECO", 2)
    job = jobs.ensure_job(db, "index_document", f"doc:{d.id}", company_id=d.company_id, document_id=d.id)
    assert jobs.claim(db, job.id, "crashed-worker")
    assert not jobs.claim(db, job.id, "other")                  # lease still valid
    job.lease_expires_at = utcnow() - timedelta(seconds=1)       # simulate the worker dying (no heartbeat)
    db.commit()
    assert jobs.recover_abandoned(db) == 1
    db.refresh(job)
    assert job.status == "retry" and job.error_class == "interrupted" and job.lease_owner is None
    assert jobs.claim(db, job.id, "new-worker")
    db.refresh(job)
    assert job.attempts == 2                                     # the interrupted attempt still counts


def test_transient_failures_back_off_and_stop_at_max_attempts(db, monkeypatch):
    d = _doc(db, "RETRYCO")
    calls = []
    monkeypatch.setattr(pipeline, "download_pdf", lambda url, dest, **k: calls.append(url) or (_ for _ in ()).throw(FetchError("Read timed out")))
    pipeline.index_document(db, d)
    job = db.query(IngestionJob).filter_by(key=f"doc:{d.id}").one()
    assert job.status == "retry" and job.error_class == "network" and d.status == "retrying"
    wait = (job.next_attempt_at - utcnow()).total_seconds()
    assert 20 <= wait <= 40                                        # ~30 s first backoff (±20 % jitter)
    pipeline.index_document(db, d)                                 # not due yet → no new download
    assert len(calls) == 1
    for _ in range(5):                                             # make it due and retry until it gives up
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
        pipeline.index_document(db, d)
        db.refresh(job)
    assert job.status == "failed" and job.attempts == job.max_attempts == 4 and len(calls) == 4
    assert job.error.startswith("Gave up after 4 attempts") and d.status == "failed"
    assert [jobs.backoff_seconds(n, jitter=False) for n in (1, 2, 3, 10)] == [30, 60, 120, 1800]   # bounded


def test_terminal_failure_is_preserved_with_explanation(db, monkeypatch):
    d = _doc(db, "TERMCO")
    calls = []
    monkeypatch.setattr(pipeline, "download_pdf", lambda url, dest, **k: calls.append(url) or (_ for _ in ()).throw(FetchError(f"Not found: {url}")))
    pipeline.index_document(db, d)
    pipeline.index_document(db, d)
    job = db.query(IngestionJob).filter_by(key=f"doc:{d.id}").one()
    assert job.status == "failed" and job.error_class == "not_found" and job.attempts == 1 and len(calls) == 1
    assert "no longer serves this document" in job.error and d.status == "failed" and d.error == job.error


def test_retry_after_extraction_failure_does_not_download_again(db, monkeypatch, tmp_path):
    d = _doc(db, "REUSECO")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    downloads, extracts = [], []
    monkeypatch.setattr(pipeline, "download_pdf", lambda url, dest, **k: downloads.append(url) or (pdf, "hash-reuse"))

    def flaky_extract(path, max_pages=None):
        extracts.append(path)
        if len(extracts) == 1:
            raise RuntimeError("connection reset by peer")       # transient, after the download succeeded
        return [(1, "Revenue grew. Management expects demand to improve.")]

    monkeypatch.setattr(pipeline, "extract_pages", flaky_extract)
    monkeypatch.setattr(pipeline, "get_llm", lambda: None, raising=False)
    pipeline.index_document(db, d)
    job = db.query(IngestionJob).filter_by(key=f"doc:{d.id}").one()
    assert job.status == "retry" and job.progress["stage"] == "extract" and "download" in job.progress["stages_done"]
    job.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()
    pipeline.index_document(db, d)
    db.refresh(job)
    assert d.status == "indexed" and job.status == "succeeded" and job.result["download"] == "reused"
    assert len(downloads) == 1 and len(extracts) == 2
    pipeline.index_document(db, d)                                # finished work is reused: nothing runs again
    assert len(downloads) == 1 and len(extracts) == 2


def test_worker_processes_due_jobs(db, monkeypatch, tmp_path):
    from app.workers import job_worker

    d = _doc(db, "WORKCO")
    pdf = tmp_path / "w.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(pipeline, "download_pdf", lambda url, dest, **k: (pdf, "hash-w"))
    monkeypatch.setattr(pipeline, "extract_pages", lambda path, max_pages=None: [(1, "Text of the filing.")])
    jobs.ensure_job(db, "index_document", f"doc:{d.id}", company_id=d.company_id, document_id=d.id)
    seen = None
    for _ in range(50):                                            # other tests may have left due jobs; drain until ours runs
        r = job_worker.run_once("test-worker")
        if r is None:
            break
        if db.query(IngestionJob).get(r["job"]).document_id == d.id:
            seen = r
            break
    assert seen and seen["status"] == "succeeded"
    db.refresh(d)
    assert d.status == "indexed"


@pytest.mark.parametrize("msg,cls", [("Read timed out", "network"), ("Server error '503 Service Unavailable'", "server_error"),
                                     ("429 Too Many Requests", "rate_limited"), ("Response is not a PDF", "not_pdf"),
                                     ("Host not in primary-source allow-list: x.com", "forbidden"),
                                     ("PDF exceeds 25 MB limit", "too_large"), ("something odd", "unknown"),
                                     ("Client error '403 Forbidden' for url 'https://ir.example.com/x.pdf'", "access_denied")])
def test_error_classification(msg, cls):
    assert jobs.classify_error(FetchError(msg)) == cls


def test_env_points_at_shared_test_db():
    assert os.environ["DATABASE_URL"].startswith("sqlite:///")
