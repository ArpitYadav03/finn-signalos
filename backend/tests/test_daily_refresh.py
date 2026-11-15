"""Daily refresh: schedule maths, document-refresh rules, and the manual trigger. Offline (downloads stubbed)."""
from datetime import datetime, time, timedelta

from fastapi.testclient import TestClient

from app.core.db import utcnow
from app.ingestion import pipeline
from app.ingestion.http import FetchError
from app.models import Announcement, Company
from app.workers import scheduler
from app.workers.scheduler import IST, next_run_after, parse_times


def test_schedule_next_run():
    times = parse_times("18:30, 08:30")
    assert times == [time(8, 30), time(18, 30)]
    sun_8am = datetime(2026, 11, 15, 8, 0, tzinfo=IST)
    assert next_run_after(sun_8am, times) == datetime(2026, 11, 15, 8, 30, tzinfo=IST)
    assert next_run_after(datetime(2026, 11, 15, 12, 0, tzinfo=IST), times).hour == 18
    late = next_run_after(datetime(2026, 11, 15, 19, 0, tzinfo=IST), times)
    assert (late.day, late.hour, late.minute) == (16, 8, 30)


def test_document_refresh_rules(db, monkeypatch):
    co = Company(ticker="DOCCO", company_name="DocCo Ltd")
    db.add(co)
    db.flush()
    now = utcnow()
    for i, cat in enumerate(["results", "M&A", "unclassified", "investor_meetings"]):
        db.add(Announcement(company_id=co.id, title=f"t{i}", source="test", source_url=f"https://www.bseindia.com/docco-{i}.pdf",
                            published_at=now - timedelta(hours=2), content_hash=f"docco{i}", category=cat,
                            processing_status="processed", is_sample=True))
    db.commit()
    calls = []

    def fake_download(url, dest, **kw):
        calls.append(url)
        raise FetchError("offline")

    monkeypatch.setattr(pipeline, "download_pdf", fake_download)
    out = pipeline.refresh_documents_for(db, [co], max_docs=1)
    assert out["retry_scheduled"] == 1 and out["skipped_budget"] == 1   # transient failure → retry later; budget respected
    assert all("docco-2" not in u and "docco-3" not in u for u in calls)  # routine filings never downloaded
    again = pipeline.refresh_documents_for(db, [co], max_docs=10)
    # The doc in backoff is not downloaded again before its retry time; only the not-yet-tried one is.
    assert len(calls) == 2 and again["waiting_retry"] == 1 and again["retry_scheduled"] == 1


def test_manual_refresh_trigger(db, monkeypatch):
    from app.main import app

    ran = []
    monkeypatch.setattr(scheduler, "run_now", lambda trigger: ran.append(trigger) or True)
    with TestClient(app) as c:
        assert c.post("/api/v1/admin/ingest/daily").json()["status"] == "started"
        assert ran == ["manual (Refresh now)"]
        st = c.get("/api/v1/status").json()["scheduler"]
        assert st["times_ist"] == "08:30,18:30" and st["enabled"] is False   # disabled under tests
