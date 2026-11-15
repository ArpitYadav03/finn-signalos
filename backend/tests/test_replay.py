"""Historical Alert Replay acceptance tests. All data is SYNTHETIC (RPCO, RPCO2) and flagged is_sample.

Cases: a known historical match, a non-match, missing data (no bar), insufficient volume history (< 20 prior sessions),
a revision published after the replay date (never visible), duplicate suppression, coverage restriction, publicly
available vs FINN-indexed, cancel / interrupted → resume without duplicates, and no alerts or plan changes.
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import (Alert, Announcement, Company, EventAnalysis, ManagementGuidance, Document, DocumentChunk, MonitoringPlan, Portfolio,
                        Price, ReplayRun, SourceCoverage)
from app.services import replay as R

D0 = date(2025, 1, 6)   # a Monday; synthetic sessions are weekdays only


def _sessions(n):
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


S = _sessions(30)


def _utc(d: date, hh=10):   # 10:00 UTC = 15:30 IST on that day
    return datetime(d.year, d.month, d.day, hh)


@pytest.fixture(scope="module")
def client(db):
    import app.ingestion.pipeline as pl
    from app.ingestion.http import FetchError
    from app.main import app

    def no_network(*a, **k):
        raise FetchError("network disabled in tests")

    pl.company_provider.fetch_company = no_network
    pl.market_provider.fetch_eod = lambda tickers, days=60: {t: [] for t in tickers}
    R_start = R.start
    R.start = lambda run_id: None          # tests drive the runner explicitly
    with TestClient(app) as c:
        a = Company(ticker="RPCO", company_name="Replay Co (synthetic)", sector="Test")
        b = Company(ticker="RPCO2", company_name="Replay Two (synthetic)", sector="Test")
        db.add_all([a, b])
        db.flush()
        for i, d in enumerate(S):
            close = 100.0
            vol = 1000.0
            if i == 10:
                close, vol = 94.0, 5000.0        # −6% with only 10 prior sessions: volume baseline insufficient
            if i == 25:
                close, vol = 94.0, 5000.0        # −6% on 5× the previous-20 average: the known match
            if i != 22:                          # RPCO has no bar on session 22 (missing data); RPCO2 does
                db.add(Price(company_id=a.id, trading_date=d, open=close, high=close + 1, low=close - 1, close=close, volume=vol, source="test"))
            db.add(Price(company_id=b.id, trading_date=d, open=50 + i * 0.01, high=51, low=49, close=50 + i * 0.01, volume=800, source="test"))
        db.add(SourceCoverage(company_id=a.id, source="filings_listing", last_attempt_at=_utc(S[-1]), last_success_at=_utc(S[-1]),
                              continuous_since=_utc(S[15], 0), items_last=5))
        db.add(SourceCoverage(company_id=b.id, source="filings_listing", last_attempt_at=_utc(S[-1]), last_success_at=_utc(S[-1]),
                              continuous_since=_utc(S[0], 0), items_last=5))

        def ann(h, d, role, stage, ingested=None, co=a):
            x = Announcement(company_id=co.id, title=f"Fund raise {role}", source="test", source_url=f"https://www.bseindia.com/rp-{h}.pdf",
                             published_at=_utc(d), ingested_at=ingested or _utc(d), content_hash=h, event_group="rp-raise", is_sample=True,
                             processing_status="processed", category="fund_raise")
            db.add(x)
            db.flush()
            db.add(EventAnalysis(announcement_id=x.id, category="fund_raise", importance_score=70, importance_level="high", headline=f"{role} (synthetic)",
                                 summary=role, why_it_matters="", reasoning=[], citations=[], key_metrics=[], model_name="rules-v1", prompt_version="test",
                                 requires_review=False, facts={"filing_role": role, "what_changed": f"{stage}: warrants at ₹100.00 each.",
                                                              "financial_event": {"action": "issuance", "stage": stage, "instrument": "warrants"}}))
            return x

        ann("appr", S[18], "Board / committee approval", "board_approval", ingested=_utc(S[20]))   # FINN indexed it two sessions later
        ann("appr-rep", S[19], "Disclosure", "board_approval")                                      # same text → repeat
        ann("allot", S[27], "Allotment", "allotment")                                               # after the replay end
        # guidance cut stated after the replay end (must not be visible)
        doc = Document(company_id=a.id, document_type="concall_transcript", title="RPCO call (synthetic)", status="indexed",
                       source_url="https://example.test/rp.pdf", publication_date=S[26], page_count=1, content_hash="rp-v1")
        db.add(doc)
        db.flush()
        ch = DocumentChunk(document_id=doc.id, chunk_index=0, page_number=1, content="Our FY26 revenue growth guidance is now 5% to 6%.")
        ch0 = DocumentChunk(document_id=doc.id, chunk_index=1, page_number=1, content="Our FY26 revenue growth guidance is 8% to 10%.")
        db.add_all([ch, ch0])
        db.flush()
        for c_, lo, hi, d in ((ch0, 8, 10, S[2]), (ch, 5, 6, S[26])):
            db.add(ManagementGuidance(company_id=a.id, metric="revenue_growth", guidance_type="quantitative", target_min=lo, target_max=hi, comparator="range",
                                      unit="pct", basis="reported", horizon="period", target_period="FY26", guidance_date=d, statement=c_.content,
                                      source_document_id=doc.id, source_page=1, chunk_id=c_.id, extractor="rules-v1"))
        p = MonitoringPlan(name="replay test", description="t", version=1, enabled=True,
                           spec={"tickers": ["RPCO", "RPCO2"], "logical_operator": "OR", "conditions": [
                               {"kind": "price_move", "direction": "down", "threshold": 5}, {"kind": "fundraise_update"}, {"kind": "guidance_cut"}]})
        pa = MonitoringPlan(name="replay AND", description="t", version=1, enabled=False,
                            spec={"tickers": ["RPCO"], "logical_operator": "AND", "conditions": [
                                {"kind": "price_move", "direction": "down", "threshold": 5}, {"kind": "volume", "threshold": 2}]})
        db.add_all([p, pa])
        db.commit()
        yield c
    R.start = R_start


def _ids(db):
    return db.query(MonitoringPlan).filter_by(name="replay test").one().id, db.query(MonitoringPlan).filter_by(name="replay AND").one().id


def _run(client, db, plan_id, start, end, mode="public"):
    r = client.post(f"/api/v1/plans/{plan_id}/replays", json={"start": start.isoformat(), "end": end.isoformat(), "mode": mode})
    assert r.status_code == 202, r.text
    R.execute(r.json()["id"])
    return client.get(f"/api/v1/replays/{r.json()['id']}").json()


def _session(run, d):
    return next(x for x in run["results"] if x["session"] == d.isoformat())


def test_coverage_before_running(client, db):
    pid, aid = _ids(db)
    cov = client.get(f"/api/v1/plans/{aid}/replay/coverage", params={"start": S[0].isoformat(), "end": S[24].isoformat()}).json()
    assert cov["session_count"] == 25 and cov["non_trading_days"] == (S[24] - S[0]).days + 1 - 25      # weekends are not sessions
    rp = next(c for c in cov["companies"] if c["ticker"] == "RPCO")
    assert rp["volume_ready_from"] == S[20].isoformat()          # the first session with 20 prior bars (S[0]–S[19])
    assert S[22].isoformat() in rp["missing_sessions"]
    assert any("20 prior sessions" in x for x in cov["limits"])
    late = client.post(f"/api/v1/plans/{pid}/replays", json={"start": S[0].isoformat(), "end": "2099-01-01"})
    assert late.status_code == 422 and "cannot look past" in late.json()["detail"]


def test_match_nonmatch_missing_insufficient_and_no_side_effects(client, db):
    pid, aid = _ids(db)
    alerts0, plan0 = db.query(Alert).count(), dict(db.get(MonitoringPlan, aid).spec)
    run = _run(client, db, aid, S[5], S[26])
    assert run["status"] == "succeeded" and run["plan_version"] == 1 and run["conditions"][1].startswith("volume exceeds 2×")
    m = _session(run, S[25])["matches"]
    assert m and m[0]["ticker"] == "RPCO" and m[0]["values"]["price_move"] == -6.0 and m[0]["values"]["volume"] == 4.17   # 5000 ÷ mean(19×1000 + 5000) = 4.17: known match
    assert _session(run, S[24])["matches"] == []                                                                           # non-match
    early = _session(run, S[10])
    assert early["matches"] == [] and "insufficient history (10 of 20 prior sessions)" in early["unable"][0]["reason"]   # AND not met
    miss = _session(run, S[22])
    assert any("No end-of-day bar" in u["reason"] for u in miss["unable"])                                                 # missing data
    assert db.query(Alert).count() == alerts0 and db.get(MonitoringPlan, aid).spec == plan0                                # no alerts, rule unchanged


def test_no_future_documents_revisions_and_duplicate_suppression(client, db):
    pid, _ = _ids(db)
    run = _run(client, db, pid, S[5], S[24])
    allm = [x for r in run["results"] for x in r["matches"]]
    assert not any(x.get("guidance_cut") for x in allm)                         # the cut stated on S[26] is after the replay end
    raise_hits = [x for x in allm if x.get("event_key") == "rp-raise"]
    assert len(raise_hits) == 1 and raise_hits[0]["filings"][0]["role"] == "Board / committee approval"
    assert not any(f["role"] == "Allotment" for x in raise_hits for f in x["filings"])     # the allotment on S[27] never leaks back
    assert raise_hits[0]["filings"][0]["id"] in raise_hits[0]["indexed_later"]             # public then, indexed by FINN later
    sup = [x for r in run["results"] for x in r["suppressed"]]
    assert sup and sup[0]["filings"][0]["role"] == "Repeat disclosure"
    pre = _session(run, S[10])
    assert any(u["kind"] == "filings" and "complete only from" in u["reason"] for u in pre["unable"] if u["ticker"] == "RPCO")
    # FINN-indexed mode: FINN indexed the second filing (S[19]) before the original approval (indexed on S[20])
    idx = _run(client, db, pid, S[5], S[24], mode="indexed")
    hits = [(r["session"], x) for r in idx["results"] for x in r["matches"] if x.get("event_key") == "rp-raise"]
    assert hits[0][0] == S[19].isoformat()
    assert hits[1][0] == S[20].isoformat() and hits[1][1]["filings"][0]["role"] == "Board / committee approval"
    assert not any(r["session"] == S[18].isoformat() and r["matches"] for r in idx["results"] if r["matches"] and
                   any(x.get("event_key") == "rp-raise" for x in r["matches"]))
    # a longer replay sees the later revision and the allotment as an update
    full = _run(client, db, pid, S[5], S[29])
    later = [x for r in full["results"] for x in r["matches"]]
    assert any(x.get("guidance_cut", {}).get("current") == "5–6%" for x in later)
    assert any(x.get("event_key") == "rp-raise" and x["kind"] == "update" for x in later)


def test_cancel_interrupt_resume_and_double_submit(client, db):
    _, aid = _ids(db)
    body = {"start": S[5].isoformat(), "end": S[26].isoformat()}
    r1 = client.post(f"/api/v1/plans/{aid}/replays", json=body).json()
    r2 = client.post(f"/api/v1/plans/{aid}/replays", json=body).json()
    assert r2["duplicate_of"] == r1["id"]                               # double submit → the same run
    client.post(f"/api/v1/replays/{r1['id']}/cancel")
    assert client.get(f"/api/v1/replays/{r1['id']}").json()["status"] == "cancelled"
    # interrupted mid-run: partial results saved, worker gone
    run = db.get(ReplayRun, r1["id"])
    first = R.evaluate_session(db, R.M.PlanSpec.model_validate(run.spec), R._scope(db, R.M.PlanSpec.model_validate(run.spec)), S[5], S[4],
                               mode="public", reported={})
    run.results, run.status, run.heartbeat_at = [first], "running", datetime(2020, 1, 1)
    db.commit()
    assert client.get(f"/api/v1/replays/{r1['id']}").json()["status"] == "interrupted"
    assert client.post(f"/api/v1/replays/{r1['id']}/resume").status_code == 202
    R.execute(r1["id"])
    done = client.get(f"/api/v1/replays/{r1['id']}").json()
    sessions = [x["session"] for x in done["results"]]
    assert done["status"] == "succeeded" and len(sessions) == len(set(sessions)) == done["progress"]["total"]   # resumed, no duplicates
    assert client.post(f"/api/v1/replays/{r1['id']}/resume").status_code == 409
