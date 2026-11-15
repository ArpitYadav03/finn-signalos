"""Regression tests for the review fixes. All data here is SYNTHETIC (company DUPCO) and flagged is_sample;
network adapters are stubbed so the suite is deterministic offline."""
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.db import utcnow
from app.ingestion.http import FetchError
from app.models import (Announcement, Company, Document, DocumentChunk, FinancialMetric, Portfolio, PortfolioHolding, Price)
from app.services import filing_facts as ff
from app.services import finance

T = "DUPCO"
LETTER_RBI = """Regd. Office: Dup Co Bank House, Mumbai – 400 013 Ref. No. SE/1 November 12, 2026 BSE Limited Dear Sir/Madam,
Sub: Approval from the Reserve Bank of India for re-appointment of Mr. A. B. Kumar as the Whole-time Director (designated as
Executive Director) of DupCo Bank Limited Ref: Disclosure under Regulation 30. In furtherance to our intimation dated September 12,
2026, we wish to inform you that the RBI vide its communication dated November 12, 2026, has given its approval for the
re-appointment of Mr. A. B. Kumar as a Whole-time Director (designated as Executive Director) of the Bank for a period of 1 (one)
year effective from November 23, 2026. Yours faithfully, For DupCo Bank Limited Jane Roe Company Secretary"""
LETTER_BOARD = """Ref. No. SE/2 November 13, 2026 Dear Sir/Madam, Sub: Re-appointment of Mr. A. B. Kumar as the Whole-time Director
(designated as Executive Director) of DupCo Bank Limited. Pursuant to approval of the Reserve Bank of India, the Board approved the
re-appointment of Mr. A. B. Kumar as a Whole-time Director (designated as Executive Director) for a period of 1 (one) year effective
from November 23, 2026, subject to approval of the shareholders of the Bank. Yours faithfully, Jane Roe Company Secretary
Annexure II PROFILE — MR. A. B. KUMAR Mr. A. B. Kumar, aged sixty (60) years, is the Executive Director of the Bank and heads Treasury."""
LETTER_EXIT = """Sub: Resignation of Mr. C. D'Souza, Chief Financial Officer. Dear Sir, We wish to inform you that Mr. C. D'Souza has resigned as
Chief Financial Officer of the Company with immediate effect citing personal reasons. Yours faithfully, Company Secretary"""


@pytest.fixture(scope="module")
def client(db):
    import app.ingestion.pipeline as pl
    from app.main import app

    def no_network(*a, **k):
        raise FetchError("network disabled in tests")

    pl.company_provider.fetch_company = no_network
    pl.market_provider.fetch_eod = lambda tickers, days=60: {t: [] for t in tickers}
    with TestClient(app) as c:
        _seed(db)
        yield c


def _filing(db, co, title, text, when, h):
    a = Announcement(company_id=co.id, title=title, source="test", source_url=f"https://www.bseindia.com/test-{h}.pdf",
                     published_at=when, content_hash=h, event_group=h, is_sample=True)
    db.add(a)
    d = Document(company_id=co.id, document_type="exchange_filing", title=title, source_url=a.source_url, status="indexed",
                 publication_date=when.date(), page_count=1)
    db.add(d)
    db.flush()
    db.add(DocumentChunk(document_id=d.id, chunk_index=0, page_number=1, content=text))
    return a


def _seed(db):
    co = Company(ticker=T, company_name="DupCo Bank Ltd", sector="Test", is_financial=True)
    db.add(co)
    db.flush()
    db.add(PortfolioHolding(portfolio_id=db.query(Portfolio).first().id, company_id=co.id))
    for p, end, rev, intr, pat in [("Q2FY26", date(2025, 9, 30), 1000, 600, 150), ("Q1FY27", date(2026, 6, 30), 1050, 620, 160),
                                   ("Q2FY27", date(2026, 9, 30), 1100, 640, 170)]:
        for n, v in (("revenue", rev), ("interest", intr), ("financing_profit", -50), ("pbt", pat * 1.3), ("pat", pat)):
            db.add(FinancialMetric(company_id=co.id, fiscal_period=p, period_end=end, period_type="quarter",
                                   statement_basis="consolidated", metric_name=n, metric_value=v, source="test"))
    db.add(FinancialMetric(company_id=co.id, fiscal_period="FY26", period_end=date(2026, 3, 31), period_type="annual",
                           statement_basis="consolidated", metric_name="revenue", metric_value=4000, source="test"))
    # Prices: the last bar duplicates the previous one entirely (open/high/low/close) -> stale provider bar
    d0 = date(2026, 10, 1)
    for i in range(24):
        c = 100 + i
        db.add(Price(company_id=co.id, trading_date=d0 + timedelta(days=i), open=c - 1, high=c + 1, low=c - 2, close=c,
                     volume=1000, source="test"))
    db.add(Price(company_id=co.id, trading_date=d0 + timedelta(days=24), open=122, high=124, low=121, close=123.0,
                 volume=1005, source="test"))
    now = utcnow()
    _filing(db, co, "Disclosure Under Regulation 30 Of The SEBI (LODR) Regulations, 2015", LETTER_RBI, now - timedelta(hours=30), "dup-rbi")
    _filing(db, co, "Announcement under Regulation 30 (LODR)-Change in Directorate", LETTER_BOARD, now - timedelta(hours=6), "dup-board")
    db.commit()
    from app.ingestion.pipeline import process_pending

    process_pending(db)


# ---------------------------------------------------------------- facts & dedup (unit)

def test_letter_facts_ignore_signature_block():
    f = ff.management_facts(LETTER_RBI)
    assert f.people[0] == "A. B. Kumar" and f.role == "Executive Director" and f.action == "reappointment"
    assert f.term == "1 year" and f.effective == "November 23, 2026" and f.regulator_approval == "RBI"
    assert "Company Secretary" != f.role  # the signatory's title must not leak in


def test_same_person_same_change_has_same_event_key():
    a = ff.management_facts(LETTER_RBI).as_dict()
    b = ff.management_facts(LETTER_BOARD).as_dict()
    assert ff.event_key("management_change", a) == ff.event_key("management_change", b) == "mgmt:kumar:continuity"


def test_continuity_scores_below_disruption():
    mods_c, why_c = ff.management_assessment(ff.management_facts(LETTER_BOARD))
    mods_d, why_d = ff.management_assessment(ff.management_facts(LETTER_EXIT))
    assert sum(p for _, p in mods_c) < 0 < sum(p for _, p in mods_d)
    assert why_c.startswith("Leadership continuity") and "shareholder approval is still pending" not in why_d
    assert "immediate effect" in why_d and "personal reasons" in why_d
    f = ff.management_facts(LETTER_EXIT)
    assert f.people == ["C. D'Souza"] and f.role == "CFO"  # apostrophes in names survive


def test_names_dedupe_substrings():
    assert ff.people("Mr. V. Srinivasa Rangan ... Mr. Rangan said") == ["V. Srinivasa Rangan"]


# ---------------------------------------------------------------- arithmetic & missing data

def test_bank_nii_and_stale_bar(client, db):
    from app.services.market import snapshot
    from app.services.metrics import results_snapshot

    co = db.query(Company).filter_by(ticker=T).one()
    lines = {l["metric"]: l for l in results_snapshot(db, co)["lines"]}
    assert lines["nii"]["value"] == 460 and lines["nii"]["yoy"]["value"] == pytest.approx(15.0)   # 1100-640 vs 1000-600
    assert "financing_profit" not in lines
    s = snapshot(db, co.id)
    assert s["stale_suspect"] and s["return_pct"]["value"] is None and "repeated" in s["return_pct"]["note"]


def test_verified_unchanged_close_is_zero(db):
    from app.services.market import snapshot

    co = Company(ticker="FLATCO", company_name="FlatCo Ltd")
    db.add(co)
    db.flush()
    d0 = date(2026, 10, 1)
    for i in range(22):
        db.add(Price(company_id=co.id, trading_date=d0 + timedelta(days=i), open=99, high=101, low=98, close=100.0, volume=1000, source="test"))
    db.add(Price(company_id=co.id, trading_date=d0 + timedelta(days=22), open=100.5, high=102, low=99, close=100.0, volume=900, source="test"))
    db.commit()
    s = snapshot(db, co.id)
    assert s["return_pct"]["value"] == 0.0 and not s["stale_suspect"]   # same close, but a genuine new session


def test_missing_data_paths():
    assert finance.yoy_pct(10, None).note == "missing value"
    assert finance.volume_ratio(100, []).value is None
    assert ff.size_context(None, 1000) is None and ff.size_context(5, None) is None
    assert ff.size_context(0.016, 170000).endswith("0.000% of annual revenue")


# ---------------------------------------------------------------- briefing groups one event, keeps both sources

def test_duplicate_filings_become_one_event(client):
    b = client.get("/api/v1/signals?hours=48").json()
    cards = [c for c in b["signals"] + b["routine"] if c["ticker"] == T]
    assert len(cards) == 1, [c["headline"] for c in cards]
    card = cards[0]
    assert card["headline"].startswith("A. B. Kumar reappointed as Executive Director")
    assert len(card["related"]) == 1                                  # both filings preserved
    urls = {c["source_url"] for c in card["citations"]}
    assert {"https://www.bseindia.com/test-dup-rbi.pdf", "https://www.bseindia.com/test-dup-board.pdf"} <= urls
    assert any("Event groups 2 filings" in r["label"] for r in card["reasons"])
    assert any("Continuity" in r["label"] for r in card["reasons"])
    assert card["why_it_matters"].startswith("Leadership continuity, not disruption")


# ---------------------------------------------------------------- push -> pull, citations, evidence lens

def test_push_to_pull_investigation_flow(client):
    card = next(c for c in client.get("/api/v1/signals?hours=48").json()["signals"] + client.get("/api/v1/signals?hours=48").json()["routine"]
                if c["ticker"] == T)
    assert client.get(f"/api/v1/signals/{card['id']}").json()["id"] == card["id"]
    r = client.post("/api/v1/research", json={"ticker": T, "question": "What has management said about leadership continuity?",
                                              "filing_id": card["id"]}).json()
    assert r["answer_markdown"].startswith("**Filing in focus:**")
    ev = {e["id"]: e for e in r["evidence"]}
    markers = set(re.findall(r"\[(E\d+)\]", r["answer_markdown"]))
    assert markers and markers <= set(ev)                                # every citation resolves
    filing_ev = [e for e in ev.values() if e.get("document_type") == "exchange_filing" and e.get("chunk_id")]
    assert filing_ev                                                     # the filing itself is cited at passage level
    detail = client.get(f"/api/v1/evidence/chunks/{filing_ev[0]['chunk_id']}").json()
    assert detail["page_number"] == filing_ev[0]["page_number"] and "Kumar" in detail["content"]
    page = client.get(f"/api/v1/documents/{detail['document']['id']}/pages/{detail['page_number']}").json()
    assert any(p["chunk_id"] == detail["chunk_id"] for p in page["passages"])


# ---------------------------------------------------------------- alerts: create, evaluate, timestamp, history

def test_alert_evaluation_is_recorded(client):
    spec = client.post("/api/v1/alert-rules/parse", json={"text": f"alert me if {T} volume exceeds 0.5x"}).json()["spec"]
    rule = client.post("/api/v1/alert-rules", json={"spec": spec}).json()
    assert rule["last_evaluation"]["eod_session"] == "2026-10-25" and rule["last_evaluation"]["matched"] == [T]
    out = client.post("/api/v1/alert-rules/evaluate").json()
    assert out["last_run"]["at"] and out["last_run"]["rules_evaluated"] >= 1 and out["new_alerts"] == 0   # deduped
    hist = [a for a in client.get("/api/v1/alerts").json() if a["rule_id"] == rule["id"]]
    assert len(hist) == 1 and "volume" in hist[0]["message"]
    client.delete(f"/api/v1/alert-rules/{rule['id']}")


# ---------------------------------------------------------------- polish pass: Event DNA, windows, portfolio, alerts, research, ledger

def test_event_dna_names_what_each_filing_added(client):
    assert ff.filing_role(LETTER_RBI) == "Regulatory approval"
    assert ff.filing_role("Copy of newspaper advertisement published regarding the notice of board meeting") == "Repeat (newspaper copy)"
    card = next(c for c in client.get("/api/v1/signals?hours=48").json()["signals"] if c["ticker"] == T)
    sup = card["supporting"]
    assert len(sup) == 2 and sup[0]["published_at"] < sup[1]["published_at"]          # oldest first
    assert sup[0]["role"] == "Regulatory approval" and sup[1]["role"] != "Repeat disclosure"
    assert all(s["added"] for s in sup)


def test_custom_window_bounds(client):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ok = client.get("/api/v1/signals", params={"since": (now - timedelta(hours=10)).isoformat(), "until": now.isoformat()})
    assert ok.status_code == 200 and ok.json()["window"]["custom"]
    tickers = [c["ticker"] for c in ok.json()["signals"] + ok.json()["routine"]]
    assert T in tickers                                   # the 6h-old board filing falls inside the custom bounds
    bad = client.get("/api/v1/signals", params={"since": now.isoformat(), "until": (now - timedelta(hours=1)).isoformat()})
    assert bad.status_code == 422


def test_portfolio_summary_counts_and_unavailable(client):
    p = client.get("/api/v1/portfolio").json()
    s = p["summary"]
    assert s["up"] + s["down"] + s["unchanged"] + s["unavailable"] == len(p["holdings"])
    assert s["unavailable"] >= 1                          # DUPCO's repeated bar is unknown, not 0.0%
    assert s["equal_weight_n"] == len(p["holdings"]) - s["unavailable"]   # holdings with no data at all also count as unavailable


def test_holdings_add_remove_validation(client):
    assert client.post("/api/v1/portfolio/holdings", json={"ticker": T}).status_code == 409   # already held
    assert client.post("/api/v1/portfolio/holdings", json={"ticker": "NOSUCHCO"}).status_code == 404  # unresolvable, network off
    assert client.delete("/api/v1/portfolio/holdings/NOSUCHCO").status_code == 404
    assert client.delete(f"/api/v1/portfolio/holdings/{T}").status_code == 204
    assert T not in [h["ticker"] for h in client.get("/api/v1/portfolio").json()["holdings"]]
    assert client.post("/api/v1/portfolio/holdings", json={"ticker": T}).status_code == 201   # known company: no network needed


def test_role_filtered_alert_does_not_match_other_roles(client):
    from app.services import alerts as alert_svc

    res = client.post("/api/v1/alert-rules/parse", json={"text": "Alert me on any CFO or CEO change"}).json()
    cond = res["spec"]["conditions"][0]
    assert cond["metric"] == "announcement_category" and sorted(cond["roles"]) == ["CEO", "CFO"]
    rule = client.post("/api/v1/alert-rules", json={"spec": res["spec"]}).json()
    assert T not in rule["last_evaluation"]["matched"]    # an Executive Director reappointment is not a CEO/CFO change
    assert rule["preview"]["scope"].startswith("All ") and "CEO or CFO" in rule["preview"]["conditions"][0]
    client.delete(f"/api/v1/alert-rules/{rule['id']}")
    dg = alert_svc.parse_rule_text("alert me on any credit rating downgrade", [])
    assert dg.spec.conditions[0].rating_action == "downgrade"


def test_unsupported_conditions_are_explained_before_saving():
    from app.services import alerts as alert_svc

    w = alert_svc.unsupported_in("alert me when RSI crosses 70 intraday or the price hits ₹3000")
    assert len(w) >= 2
    spec = alert_svc.AlertRuleSpec.model_validate({"rule_type": "market", "logical_operator": "OR", "conditions": [
        {"metric": "abs_daily_return_pct", "operator": "gte", "value": 5}, {"metric": "volume_ratio", "operator": "gt", "value": 2}]})
    pv = alert_svc.preview(spec, None, 18)
    assert pv["scope"] == "All 18 holdings" and "ANY" in pv["combine"] and "IST" in pv["frequency"]


def test_editing_a_threshold_re_evaluates_the_rule(client):
    spec = client.post("/api/v1/alert-rules/parse", json={"text": f"alert me if {T} volume exceeds 0.5x"}).json()["spec"]
    rule = client.post("/api/v1/alert-rules", json={"spec": spec}).json()
    assert rule["last_evaluation"]["status"] == "matched"
    edit = rule["conditions_editable"][0]
    assert edit["metric"] == "volume_ratio" and edit["operator"] == "gt"
    spec["conditions"][edit["index"]]["value"] = 50
    out = client.patch(f"/api/v1/alert-rules/{rule['id']}", json={"spec": spec}).json()
    assert out["conditions_editable"][0]["value"] == 50 and out["enabled"]
    assert out["last_evaluation"]["status"] == "no_match"
    client.delete(f"/api/v1/alert-rules/{rule['id']}")


def test_follow_up_is_split_from_answer():
    from app.agents.research_agent import split_follow_up

    co = Company(ticker="X", company_name="X Ltd", is_financial=False)
    a, q = split_follow_up("Revenue grew [M].\n\n**Limits:** one quarter only.\nFollow-up: How did margins move?", ["results"], co)
    assert q == "How did margins move?" and "Follow-up" not in a and a.endswith("one quarter only.")
    _, q3 = split_follow_up("Done.\nFollow-up: Would you like details on the segment-wise performance from the Q2 FY27 presentation?", ["general"], co)
    assert q3 == "What are the details of the segment-wise performance from the Q2 FY27 presentation?"
    a2, q2 = split_follow_up("No follow-up line here.", ["general"], co)
    assert a2 == "No follow-up line here." and q2


def test_ledger_plain_explanations_and_sections():
    from app.services import promise

    assert promise.plain("Guidance stated in constant currency; reported INR growth differs").startswith("Guidance is in constant currency")
    base = {"guidance_type": "quantitative", "metric": "revenue_growth", "horizon": "fy", "comparison": {"status": "met"}}
    assert promise._section(base) == "commitment"
    assert promise._section({**base, "horizon": "long_term", "comparison": {"status": "long_term"}}) == "aspiration"
    assert promise._section({**base, "guidance_type": "qualitative"}) == "other"


def test_fund_raise_approval_and_allotment_share_identity():
    approve = "Approved issue of 5.74 crore warrants to Bajaj Finserv at ₹1,010 each, totaling ₹5,799.99 crore."
    allot = "Announcement under Regulation 30 (LODR)-Allotment. Approved allotment of 5.74 crore warrants to Bajaj Finserv at Rs 1,010 each."
    qip = "Bajaj Finance opens qualified institutions placement with floor price of ₹992.25"
    assert ff.event_key("fund_raise", {}, approve) == ff.event_key("fund_raise", {}, allot) == "fund:warrants:1010"
    assert ff.event_key("fund_raise", {}, qip) == "fund:qip:992.25"
    assert ff.event_key("fund_raise", {}, "Intimation of board meeting") is None


def test_results_link_targets_the_figures_page():
    from types import SimpleNamespace as NS

    from app.services.metrics import _figures_chunk, _number_variants

    assert {"4229", "4,229"} <= _number_variants(4229.07) and "1,23,456" in _number_variants(123456.2)
    cover = NS(content="The Manager, Listing Department ... 2nd November, 2026")
    standalone = NS(content="Standalone results: Revenue 18,021.33 Profit 4,100.10")
    consolidated = NS(content="Statement of Unaudited Consolidated Financial Results ... Net profit 4229.07 ... PBT 5694.62")
    assert _figures_chunk([cover, standalone, consolidated], [4229.07, 5694.62], "consolidated") is consolidated
    assert _figures_chunk([cover], [4229.07], "consolidated") is None


def test_tighten_keeps_role_and_downgrade_filters():
    from app.services import alerts as alert_svc

    broad = {"metric": "announcement_category", "operator": "in", "value": ["management_change"]}
    narrow = {**broad, "roles": ["CEO", "CFO"]}
    spec = alert_svc.AlertRuleSpec.model_validate({"rule_type": "announcement", "logical_operator": "OR", "conditions": [broad, narrow]})
    out = alert_svc.tighten(spec, "Alert me on any CEO or CFO change")
    assert len(out.conditions) == 1 and out.conditions[0].roles == ["CEO", "CFO"]       # broad duplicate dropped
    only_broad = alert_svc.AlertRuleSpec.model_validate({"rule_type": "announcement", "conditions": [broad]})
    assert alert_svc.tighten(only_broad, "tell me if the CFO quits").conditions[0].roles == ["CFO"]
    assert alert_svc.tighten(only_broad, "any leadership change").conditions[0].roles is None
    rating = alert_svc.AlertRuleSpec.model_validate({"rule_type": "announcement", "conditions": [
        {"metric": "announcement_category", "operator": "in", "value": ["credit_rating"]}]})
    assert alert_svc.tighten(rating, "any rating downgrade").conditions[0].rating_action == "downgrade"


def test_empty_ledger_reports_transcript_coverage(client):
    pt = client.get(f"/api/v1/companies/{T}/promise-tracker").json()
    assert pt["items"] == [] and pt["coverage"] == {"transcripts_indexed": 0, "transcripts_listed": 0, "periods": []}


# ---------------------------------------------------------------- review workflow ("Since your last review")

def test_review_workflow_is_explicit_and_tracks_updates(client, db):
    from app.models import Announcement, EventAnalysis, EventReview

    r0 = client.get("/api/v1/review").json()
    assert r0["first_visit"] is True
    client.get("/api/v1/review")
    assert db.query(EventReview).count() == 0                          # viewing never marks anything reviewed
    card = next(c for c in r0["new"] if c["ticker"] == T)
    key = card["event_key"]
    assert any(m["kind"] == "shareholder_approval" for m in card["milestones"])   # "subject to approval of the shareholders"
    assert client.post("/api/v1/review/start").json()["started_at"]
    assert client.get("/api/v1/review").json()["first_visit"] is False

    out = client.post(f"/api/v1/review/{key}", json={"action": "watch", "milestone": "shareholder_approval"}).json()
    assert out["status"] == "watching" and out["watch"]["is_alert"] is False   # suggested monitoring, not an alert
    r1 = client.get("/api/v1/review").json()
    assert key not in {c["event_key"] for c in r1["new"]} and key in {c["event_key"] for c in r1["unresolved"]}

    # A later filing in the same event: the shareholder vote.
    first = db.query(Announcement).filter_by(event_group=key).first()
    a = Announcement(company_id=first.company_id, title="Outcome of Postal Ballot — re-appointment of Mr. A. B. Kumar", source="test",
                     source_url="https://www.bseindia.com/test-dup-ballot.pdf", published_at=utcnow(), content_hash="dup-ballot",
                     event_group=key, is_sample=True, processing_status="processed", category="postal_ballot")
    db.add(a)
    db.flush()
    db.add(EventAnalysis(announcement_id=a.id, category="postal_ballot", importance_score=30, importance_level="low",
                         headline="Shareholders approved the re-appointment of A. B. Kumar", summary="Postal ballot passed",
                         why_it_matters="Ratifies the continuity decision.", reasoning=[], citations=[], facts={"filing_role": "Shareholder approval"},
                         key_metrics=[], model_name="rules-v1", prompt_version="test", requires_review=False))
    db.commit()
    r2 = client.get("/api/v1/review").json()
    upd = next(c for c in r2["updated"] if c["event_key"] == key)
    assert upd["review"]["update"] == "Milestone reached" and upd["review"]["new_filings"][0]["id"] == a.id
    assert key not in {c["event_key"] for c in r2["unresolved"]}
    client.post(f"/api/v1/review/{key}", json={"action": "reviewed"})
    r3 = client.get("/api/v1/review").json()
    assert key not in {c["event_key"] for c in r3["updated"] + r3["new"] + r3["unresolved"]} and r3["reviewed_without_changes"] >= 1
    assert client.post(f"/api/v1/review/{key}", json={"action": "nope"}).status_code == 422
    client.post(f"/api/v1/review/{key}", json={"action": "reopen"})
    assert key in {c["event_key"] for c in client.get("/api/v1/review").json()["new"]}
