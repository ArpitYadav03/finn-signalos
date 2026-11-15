"""Investor Handover acceptance tests. All data is SYNTHETIC (company HOCO) and flagged is_sample.

Covers: one saved plan, an equivalent repeated disclosure, a meaningful update, a source failure, a return visit,
explicit versioned review (viewing marks nothing), idempotent evaluation, edits/pauses, and persistence across sessions.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal, utcnow
from app.models import (Announcement, Company, Document, DocumentChunk, EventAnalysis, EventReview, ManagementGuidance,
                        MonitoringPlan, PlanEvaluation, Portfolio, PortfolioHolding, SourceCoverage)

T = "HOCO"
TEXT = "Watch HOCO for guidance cuts, CFO resignations and changes to announced fundraising."


def _fe(stage):
    return {"instrument": "warrants", "instrument_family": "equity", "action": "issuance", "capital_direction": "inflow", "stage": stage,
            "amount": 500.0, "unit": "crore", "unit_price": 100.0, "isins": [], "dates": [], "evidence": {"stage": f"approved the {stage}"}}


def _ann(db, co, h, when, role, stage, what, group="hoco-raise"):
    a = Announcement(company_id=co.id, title=f"Fund raise — {role}", source="test", source_url=f"https://www.bseindia.com/test-{h}.pdf",
                     published_at=when, content_hash=h, event_group=group, is_sample=True, processing_status="processed", category="fund_raise")
    db.add(a)
    db.flush()
    db.add(EventAnalysis(announcement_id=a.id, category="fund_raise", importance_score=70, importance_level="high", headline=what,
                         summary=what, why_it_matters="Equity raise: dilution.", reasoning=[], citations=[], key_metrics=[],
                         facts={"filing_role": role, "what_changed": what, "financial_event": _fe(stage)}, model_name="rules-v1",
                         prompt_version="test", requires_review=False))
    db.commit()
    return a


@pytest.fixture(scope="module")
def client(db):
    import app.ingestion.pipeline as pl
    from app.ingestion.http import FetchError
    from app.main import app

    def no_network(*a, **k):
        raise FetchError("network disabled in tests")

    pl.company_provider.fetch_company = no_network
    pl.market_provider.fetch_eod = lambda tickers, days=60: {t: [] for t in tickers}
    with TestClient(app) as c:
        co = Company(ticker=T, company_name="HoCo Industries Ltd", sector="Test")
        db.add(co)
        db.flush()
        pf = db.query(Portfolio).first() or Portfolio(name="Test")
        db.add(pf)
        db.flush()
        db.add(PortfolioHolding(portfolio_id=pf.id, company_id=co.id))
        now = utcnow()
        db.add(SourceCoverage(company_id=co.id, source="filings_listing", last_attempt_at=now - timedelta(hours=2),
                              last_success_at=now - timedelta(hours=2), continuous_since=now - timedelta(days=60), items_last=5))
        # guidance: 10–12% revenue growth (FY27, constant currency) cut to 8–9%
        doc = Document(company_id=co.id, document_type="earnings_call", title="HoCo Q2 FY27 earnings call", status="indexed",
                       source_url="https://example.test/hoco-call.pdf", publication_date=date.today() - timedelta(days=5), page_count=3,
                       content_hash="hoco-call-v1")
        db.add(doc)
        db.flush()
        ch_old = DocumentChunk(document_id=doc.id, chunk_index=0, page_number=2, content="For FY27 our revenue growth guidance is 10% to 12% in constant currency.")
        ch_new = DocumentChunk(document_id=doc.id, chunk_index=1, page_number=3, content="We are revising our FY27 revenue growth guidance to 8% to 9% in constant currency.")
        db.add_all([ch_old, ch_new])
        db.flush()
        for ch, lo, hi, d in ((ch_old, 10, 12, date.today() - timedelta(days=100)), (ch_new, 8, 9, date.today() - timedelta(days=5))):
            db.add(ManagementGuidance(company_id=co.id, metric="revenue_growth", guidance_type="quantitative", target_min=lo, target_max=hi,
                                      comparator="range", unit="pct", basis="constant_currency", horizon="period", target_period="FY27",
                                      guidance_date=d, statement=ch.content, source_document_id=doc.id, source_page=ch.page_number,
                                      chunk_id=ch.id, extractor="rules-v1"))
        db.commit()
        yield c
        from app.models import UserPreference   # leave the shared review state as other test modules expect it

        db.query(UserPreference).filter_by(key="review_state").delete()
        db.query(EventReview).filter_by(company_id=co.id).delete()
        db.commit()


def _plan(client):
    d = client.post("/api/v1/plans/draft", json={"text": TEXT}).json()
    return client.post("/api/v1/plans", json={"name": "HoCo handover", "description": TEXT, "spec": d["spec"]})


def test_draft_keeps_scope_roles_and_explains_limits(client):
    d = client.post("/api/v1/plans/draft", json={"text": TEXT}).json()
    kinds = {c["kind"]: c for c in d["spec"]["conditions"]}
    assert d["spec"]["tickers"] == [T] and set(kinds) == {"guidance_cut", "fundraise_update", "filing"}
    assert kinds["filing"]["roles"] == ["CFO"] and kinds["filing"]["change"] == "departure" and d["spec"]["logical_operator"] == "OR"
    assert "08:30" in d["preview"]["frequency"]
    u = client.post("/api/v1/plans/draft", json={"text": "Watch HOCO for RSI above 70 and credit rating downgrades"}).json()
    assert u["unsupported"] == ["technical indicators"] and u["spec"]["conditions"][0]["rating_action"] == "downgrade"
    q = client.post("/api/v1/plans/draft", json={"text": "Tell me if HOCO falls sharply"}).json()
    assert q["spec"] is None and q["clarification"]["field"] == "price_threshold"       # one question, no guessed threshold
    a = client.post("/api/v1/plans/draft", json={"text": "Tell me if HOCO falls sharply", "clarification": 5}).json()
    assert a["spec"]["conditions"] == [{"kind": "price_move", "categories": None, "roles": None, "rating_action": None,
                                        "change": None, "direction": "down", "threshold": 5.0}]
    bad = client.post("/api/v1/plans", json={"name": "x", "description": "x", "spec": {"conditions": [
        {"kind": "filing", "categories": ["fund_raise"]}, {"kind": "volume", "threshold": 2}], "logical_operator": "AND"}})
    assert bad.status_code == 422


def test_handover_end_to_end(client, db):
    co = db.query(Company).filter_by(ticker=T).one()
    now = utcnow()
    approval = _ann(db, co, "hoco-appr", now - timedelta(days=3), "Board / committee approval", "board_approval",
                    "Board / committee approval: warrants worth ₹500 crore at ₹100.00 each.")
    r = _plan(client)
    assert r.status_code == 201
    plan = r.json()
    assert plan["version"] == 1 and plan["enabled"] and plan["last_change"] is not None
    # duplicate submit returns the same plan
    again = _plan(client).json()
    assert again["id"] == plan["id"] and again["duplicate_of"] == plan["id"]
    assert db.query(MonitoringPlan).count() == 1

    b = client.get(f"/api/v1/plans/{plan['id']}/briefing").json()
    client.get(f"/api/v1/plans/{plan['id']}/briefing")
    assert db.query(EventReview).filter(EventReview.company_id == co.id).count() == 0   # viewing marks nothing
    new = {i["review_key"]: i for i in b["sections"]["new"]}
    ev = new["hoco-raise"]
    assert ev["version_ids"] == [approval.id] and "fund raise" in ev["matched"][0]
    cut = next(i for i in b["sections"]["new"] if i["kind"] == "guidance_cut")
    assert cut["guidance_cut"]["previous"] == "10–12%" and cut["guidance_cut"]["current"] == "8–9%"
    assert [e["document"]["passage"] for e in cut["evidence"]] == [
        "We are revising our FY27 revenue growth guidance to 8% to 9% in constant currency.",
        "For FY27 our revenue growth guidance is 10% to 12% in constant currency."]       # verbatim from the chunks
    assert cut["evidence"][0]["document"]["document_version"] == "hoco-call-v1" and cut["evidence"][0]["document"]["page"] == 3
    assert any(q["question"].startswith("Allotment") for q in b["sections"]["unresolved"])

    # explicit review with the version the investor saw; a filing that lands meanwhile is not marked reviewed
    allot = _ann(db, co, "hoco-allot", now - timedelta(hours=5), "Allotment", "allotment", "Allotment: warrants at ₹100.00 each.")
    client.post("/api/v1/plans/items/review", json={"review_key": "hoco-raise", "ticker": T, "version_ids": ev["version_ids"]})
    client.post("/api/v1/plans/items/review", json={"review_key": cut["review_key"], "ticker": T, "version_ids": cut["version_ids"]})
    client.post("/api/v1/plans/items/review", json={"review_key": cut["review_key"], "ticker": T, "version_ids": cut["version_ids"]})
    assert db.query(EventReview).filter_by(event_key=cut["review_key"]).count() == 1   # repeated click: one record

    # return visit: the allotment is a meaningful update; the earlier question is resolved
    b2 = client.get(f"/api/v1/plans/{plan['id']}/briefing").json()
    upd = next(i for i in b2["sections"]["updated"] if i["review_key"] == "hoco-raise")
    assert [f["id"] for f in upd["new_filings"]] == [allot.id] and upd["new_filings"][0]["role"] == "Allotment"
    assert any(q["resolved_by"]["id"] == allot.id for q in b2["sections"]["resolved"])
    assert not any(i["kind"] == "guidance_cut" for i in b2["sections"]["new"]) and b2["reviewed_unchanged"] >= 1
    client.post("/api/v1/plans/items/review", json={"review_key": "hoco-raise", "ticker": T, "version_ids": upd["version_ids"]})

    # an equivalent repeated disclosure is not new
    rep = _ann(db, co, "hoco-allot-rep", now - timedelta(hours=1), "Allotment", "allotment", "Allotment: warrants at ₹100.00 each.")
    b3 = client.get(f"/api/v1/plans/{plan['id']}/briefing").json()
    assert not any(i["review_key"] == "hoco-raise" for i in b3["sections"]["updated"] + b3["sections"]["new"])
    sup = next(i for i in b3["repeats_suppressed"] if i["review_key"] == "hoco-raise")
    assert sup["new_filings"][0]["id"] == rep.id and sup["new_filings"][0]["role"] == "Repeat disclosure"

    # source failure: unable to evaluate, never "no change"
    cov = db.query(SourceCoverage).filter_by(company_id=co.id).one()
    cov.last_attempt_at, cov.last_error = utcnow(), "HTTP 503 from filings index"
    db.commit()
    b4 = client.get(f"/api/v1/plans/{plan['id']}/briefing").json()
    un = next(i for i in b4["sections"]["unable"] if i["ticker"] == T)
    assert "not the same as no change" in un["reason"] and "503" in un["reason"]
    cov.last_success_at = utcnow()
    cov.last_error = None
    db.commit()

    # idempotent evaluation records; edits version the plan; pause stops scheduled evaluation
    from app.services.monitoring import evaluate_enabled

    n0 = db.query(PlanEvaluation).count()
    evaluate_enabled(db)
    evaluate_enabled(db)
    assert db.query(PlanEvaluation).count() == n0 + 1
    spec = plan["spec"]
    spec["conditions"] = [c for c in spec["conditions"] if c["kind"] != "guidance_cut"]
    e = client.put(f"/api/v1/plans/{plan['id']}", json={"name": "HoCo handover", "description": TEXT, "spec": spec}).json()
    assert e["version"] == 2 and [v["version"] for v in client.get(f"/api/v1/plans/{plan['id']}/versions").json()] == [1, 2]
    assert client.post(f"/api/v1/plans/{plan['id']}/pause").json()["enabled"] is False
    assert evaluate_enabled(db)["evaluated"] == 0
    client.post(f"/api/v1/plans/{plan['id']}/resume")

    # persistence: a fresh session (as after a restart) sees the plan and the review progress
    s2 = SessionLocal()
    try:
        p2 = s2.get(MonitoringPlan, plan["id"])
        assert p2.enabled and p2.version == 2
        assert set(s2.query(EventReview).filter_by(event_key="hoco-raise").one().filings_seen) == {approval.id, allot.id}
    finally:
        s2.close()


def test_cfo_resignation_matches_but_cfo_appointment_does_not(client, db):
    co = db.query(Company).filter_by(ticker=T).one()
    plan = db.query(MonitoringPlan).first()
    for h, action, role in (("hoco-cfo-out", "departure", "CFO"), ("hoco-cfo-in", "appointment", "CFO"), ("hoco-cs-out", "departure", "Company Secretary")):
        a = Announcement(company_id=co.id, title=f"Change in KMP {h}", source="test", source_url=f"https://www.bseindia.com/test-{h}.pdf",
                         published_at=utcnow() - timedelta(hours=2), content_hash=h, event_group=h, is_sample=True,
                         processing_status="processed", category="management_change")
        db.add(a)
        db.flush()
        db.add(EventAnalysis(announcement_id=a.id, category="management_change", importance_score=60, importance_level="high", headline=h,
                             summary=h, why_it_matters="", reasoning=[], citations=[], key_metrics=[], facts={"role": role, "action": action},
                             model_name="rules-v1", prompt_version="test", requires_review=False))
    db.commit()
    keys = {i["review_key"] for i in client.get(f"/api/v1/plans/{plan.id}/briefing").json()["sections"]["new"]}
    assert "hoco-cfo-out" in keys and "hoco-cfo-in" not in keys and "hoco-cs-out" not in keys


def test_review_feed_also_ignores_repeats(client, db):
    """The Briefing 'Since your last review' feed and plans agree: a repeat filing does not reopen a reviewed event."""
    feed = client.get("/api/v1/review?days=30").json()
    assert "hoco-raise" not in {c["event_key"] for c in feed["updated"]}


def test_market_check_and_or_semantics():
    from app.services.monitoring import PlanSpec, market_check

    snap = lambda r, v, rn=None, vn=None: {"return_pct": {"value": r, "note": rn}, "volume_ratio": {"value": v, "note": vn}}  # noqa: E731
    both = PlanSpec(conditions=[{"kind": "price_move", "direction": "down", "threshold": 4}, {"kind": "volume", "threshold": 2}], logical_operator="AND")
    assert market_check(both, snap(-5.0, 2.5))["result"] == "match"
    assert market_check(both, snap(-5.0, 1.5))["result"] == "no_match"            # AND: one condition false → no match
    assert market_check(both, snap(-1.0, None, vn="insufficient history (12 sessions)"))["result"] == "no_match"   # settled by the price leg
    u = market_check(both, snap(-5.0, None, vn="insufficient history (12 sessions)"))
    assert u["result"] == "unable" and "insufficient history" in u["unable"][0]["reason"]
    either = PlanSpec(conditions=both.conditions, logical_operator="OR")
    assert market_check(either, snap(-1.0, 2.5))["matched"] == ["volume exceeds 2× its previous-20-session average (end-of-day)"]
    assert market_check(either, None)["result"] == "unable"
