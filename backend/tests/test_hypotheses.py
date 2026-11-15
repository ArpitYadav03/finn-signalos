"""Competing Hypotheses acceptance tests. All companies and figures are SYNTHETIC (CASHA, CASHB, CASHC) and flagged
is_sample via their source; the model is replaced by a fake so classification and quote validation are deterministic.

Cases: conflicting evidence, insufficient evidence, incompatible periods (quarterly) and an incompatible company (lender),
premise not supported, model judgements with a non-verbatim quote discarded, versions on revisit, and Handover hand-off.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.models import Company, Document, DocumentChunk, FinancialMetric, Investigation, MonitoringPlan, Portfolio, PortfolioHolding
from app.services import hypotheses as H

Q = "Is weaker cash conversion temporary working-capital expansion or persistent deterioration?"
YEARS = ["FY22", "FY23", "FY24", "FY25", "FY26"]


def _co(db, ticker, cfo, op, wc, *, financial=False, holding=True):
    co = Company(ticker=ticker, company_name=f"{ticker.title()} Synthetic Ltd", sector="Test", is_financial=financial)
    db.add(co)
    db.flush()
    if holding:
        db.add(PortfolioHolding(portfolio_id=db.query(Portfolio).first().id, company_id=co.id))
    for i, y in enumerate(YEARS):
        for m, v in (("cfo", cfo[i]), ("operating_profit", op[i]), ("working_capital_days", wc[i]),
                     ("debtor_days", 40 + wc[i] / 2), ("inventory_days", 30.0), ("days_payable", 25.0)):
            if v is not None:
                db.add(FinancialMetric(company_id=co.id, fiscal_period=y, period_end=date(2000 + int(y[2:]), 3, 31), period_type="annual",
                                       statement_basis="consolidated", metric_name=m, metric_value=v, source="test-synthetic"))
    db.commit()
    return co


class FakeLLM:
    """Returns one judgement per passage: P1 with a verbatim quote, P2 with a fabricated quote (must be discarded)."""
    calls = 0

    def structured(self, tracker, tier, system, user, schema):
        import json

        FakeLLM.calls += 1
        data = json.loads(user)
        items = []
        for p in data["passages"]:
            if "unwind" in p["text"]:
                items.append({"ref": p["ref"], "relations": {"h1": "supports", "h2": "challenges"},
                              "quote": "we expect this working capital to unwind by the end of the year",
                              "explanation": "Management expects the build to reverse."})
            else:
                items.append({"ref": p["ref"], "relations": {"h1": "challenges", "h2": "supports"},
                              "quote": "a sentence that does not appear in the passage at all",
                              "explanation": "Fabricated."})
        return schema.model_validate({"items": items})


@pytest.fixture(scope="module")
def client(db):
    import app.ingestion.pipeline as pl
    import app.llm.client as lc
    from app.ingestion.http import FetchError
    from app.main import app

    def no_network(*a, **k):
        raise FetchError("network disabled in tests")

    pl.company_provider.fetch_company = no_network
    pl.market_provider.fetch_eod = lambda tickers, days=60: {t: [] for t in tickers}
    orig = lc.get_llm
    lc.get_llm = lambda: FakeLLM()
    with TestClient(app) as c:
        if db.query(Portfolio).first() is None:
            db.add(Portfolio(name="Test"))
            db.commit()
        # A: conversion 80/82/78 → 60/55 (two weak years) while working-capital days jump 30 → 40 in FY26 → conflicting
        a = _co(db, "CASHA", [800, 820, 780, 600, 550], [1000] * 5, [20, 22, 24, 30, 40])
        doc = Document(company_id=a.id, document_type="concall_transcript", title="CASHA Q1 FY27 earnings call (synthetic)", status="indexed",
                       source_url="https://example.test/casha.pdf", publication_date=date(2026, 8, 1), page_count=2, content_hash="casha-v1",
                       reporting_period="Q1FY27")
        db.add(doc)
        db.flush()
        db.add_all([DocumentChunk(document_id=doc.id, chunk_index=0, page_number=1,
                                  content="Receivables rose because of a large government contract; we expect this working capital to unwind by the end of the year."),
                    DocumentChunk(document_id=doc.id, chunk_index=1, page_number=2,
                                  content="Customers have asked for longer payment terms and working capital stays elevated across the business."),
                    DocumentChunk(document_id=doc.id, chunk_index=2, page_number=2,   # overlapping window repeating chunk 0
                                  content="Receivables rose because of a large government contract; we expect this working capital to unwind by the end of the year.")])
        # B: only FY26 weak and working-capital days FELL → insufficient
        _co(db, "CASHB", [800, 820, 780, 800, 600], [1000] * 5, [40, 40, 40, 40, 35])
        # C: not weaker → premise not supported
        _co(db, "CASHC", [700, 720, 710, 705, 715], [1000] * 5, [30] * 5)
        _co(db, "LENDCO", [100, -500, 300, 200, -100], [1000] * 5, [0] * 5, financial=True)
        db.commit()
        yield c
    lc.get_llm = orig


def _hyps():
    return [dict(h) for h in H.SUGGESTED]


def test_conflicting_evidence_with_inputs_and_verbatim_quotes(client, db):
    r = client.post("/api/v1/investigations/compare", json={"ticker": "CASHA", "question": Q, "hypotheses": _hyps()}).json()
    assert r["status"] == "ok" and r["summary"]["state"] == "conflicting"
    gap = next(i for i in r["items"] if i["id"].startswith("calc:conversion_gap"))
    assert gap["calculation"]["value"] == -25.0                      # 55% − median(80, 82, 78)% = −25 pp
    assert {(x["metric"], x["period"]) for x in gap["calculation"]["inputs"]} >= {("cfo", "FY26"), ("operating_profit", "FY22")}
    by = r["summary"]["by_hypothesis"]
    assert "fact:wc_change:FY26" in by["h1"]["supports"] and "calc:duration:FY26" in by["h1"]["challenges"]
    assert "calc:duration:FY26" in by["h2"]["supports"]
    docs = [i for i in r["items"] if i["kind"] == "document"]
    ai = [d for d in docs if d["classified_by"] == "ai"]
    assert len(ai) == 1 and ai[0]["document"]["passage"] == "we expect this working capital to unwind by the end of the year"
    assert ai[0]["document"]["document_version"] == "casha-v1" and ai[0]["document"]["page"] == 1
    other = next(d for d in docs if d["classified_by"] is None)   # fabricated quote → judgement discarded, passage shown as context
    assert set(other["relations"].values()) == {"context"} and r["retrieval"]["discarded"] >= 1
    assert "%" not in r["summary"]["text"] and "likely" not in r["summary"]["text"].lower()      # no invented probabilities


def test_insufficient_and_premise_and_incompatible(client):
    b = client.post("/api/v1/investigations/compare", json={"ticker": "CASHB", "question": Q, "hypotheses": _hyps()}).json()
    assert b["summary"]["state"] == "insufficient"
    assert any(m["what"].startswith("Cash flow after FY26") for m in b["missing"])          # one weak year: says what would settle it
    assert "fact:wc_change:FY26" in b["summary"]["by_hypothesis"]["h1"]["challenges"]
    c = client.post("/api/v1/investigations/compare", json={"ticker": "CASHC", "question": Q, "hypotheses": _hyps()}).json()
    assert c["summary"]["state"] == "premise_not_supported" and len(c["items"]) == 1
    q = client.post("/api/v1/investigations/compare", json={"ticker": "CASHA", "question": "Is Q2 FY27 cash conversion weaker because of working capital?",
                                                         "hypotheses": _hyps()}).json()
    assert q["status"] == "incompatible" and "full financial years" in q["reason"] and q["items"] == []
    s = client.get("/api/v1/investigations/suggest", params={"ticker": "LENDCO", "question": Q}).json()
    assert s["refusal"]["status"] == "incompatible" and s["hypotheses"] == []
    u = client.get("/api/v1/investigations/suggest", params={"ticker": "CASHA", "question": "Will margins expand next year?"}).json()
    assert u["refusal"]["status"] == "unsupported_question"
    bad = client.post("/api/v1/investigations/compare", json={"ticker": "CASHA", "question": Q, "hypotheses": [_hyps()[0], _hyps()[0]]})
    assert bad.status_code == 422


def test_save_revisit_versions_and_handover(client, db):
    calls = FakeLLM.calls
    body = {"ticker": "CASHA", "question": Q, "hypotheses": _hyps()}
    client.post("/api/v1/investigations/compare", json=body)
    inv = client.post("/api/v1/investigations", json=body).json()
    assert FakeLLM.calls == calls                                     # save re-uses the comparison: no second model call
    assert inv["current_version"] == 1 and not inv["changed_since_review"]
    assert client.post("/api/v1/investigations", json=body).json()["duplicate_of"] == inv["id"]
    assert db.query(Investigation).count() == 1
    r = client.post(f"/api/v1/investigations/{inv['id']}/refresh").json()
    assert r["evidence_changed"] is False and r["current_version"] == 1
    # new evidence arrives: FY26 restated with higher working-capital days → a new version
    co = db.query(Company).filter_by(ticker="CASHA").one()
    m = db.query(FinancialMetric).filter_by(company_id=co.id, metric_name="working_capital_days", fiscal_period="FY23").one()
    m.metric_value = 35.0      # breaks the 'rose every year' streak → the trend item disappears
    db.commit()
    H.refresh_all(db)
    g = client.get(f"/api/v1/investigations/{inv['id']}").json()
    assert g["current_version"] == 2 and g["changed_since_review"] and g["changes"] is not None
    client.get(f"/api/v1/investigations/{inv['id']}")
    assert db.get(Investigation, inv["id"]).reviewed_version == 1    # viewing does not mark it seen
    # Handover: a plan covering CASHA shows the investigation as an update
    p = MonitoringPlan(name="t", description="t", spec={"tickers": ["CASHA"], "conditions": [{"kind": "guidance_cut"}], "logical_operator": "OR"},
                       version=1, enabled=True)
    db.add(p)
    db.commit()
    b = client.get(f"/api/v1/plans/{p.id}/briefing").json()
    assert any(i["kind"] == "investigation" and i["investigation_id"] == inv["id"] for i in b["sections"]["updated"])
    client.post(f"/api/v1/investigations/{inv['id']}/reviewed", json={"version": 2})
    client.post(f"/api/v1/investigations/{inv['id']}/reviewed", json={"version": 1})   # a stale click never moves it back
    assert db.get(Investigation, inv["id"]).reviewed_version == 2
    b2 = client.get(f"/api/v1/plans/{p.id}/briefing").json()
    assert not any(i["kind"] == "investigation" for i in b2["sections"]["updated"])
    db.delete(p)
    db.commit()


def test_table_rows_are_not_judged_by_the_model():
    assert H._numeric("(Increase) / decrease in trade receivables 20,888.4 (27,141.0)")
    assert not H._numeric("Receivables rose because of a large government contract; we expect this working capital to unwind by the end of the year.")
    assert H.PERIOD_WORDS.search("Cash flow from operations remained healthy during the quarter")
    assert not H.PERIOD_WORDS.search("Working capital stays elevated across the business.")
