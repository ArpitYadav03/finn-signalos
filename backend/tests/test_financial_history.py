"""Versioned financial observations. All values are SYNTHETIC (company HISTCO)."""
from datetime import date

from app.models import Company, FinancialMetric, FinancialObservation, FinancialSnapshot
from app.services.financial_history import Candidate, history, record_snapshot

Q = [("Q1FY27", date(2026, 6, 30)), ("Q2FY27", date(2026, 9, 30)), ("Q4FY26", date(2026, 3, 31)), ("Q3FY26", date(2025, 12, 31))]


def rows(rev=None, pat=None, quarters=Q):
    rev = rev or {p: 1000 + i * 10 for i, (p, _) in enumerate(quarters)}
    pat = pat or {p: 100 + i for i, (p, _) in enumerate(quarters)}
    out = []
    for p, end in quarters:
        out.append(Candidate(p, end, "quarter", "revenue", rev[p], "crore"))
        out.append(Candidate(p, end, "quarter", "pat", pat[p], "crore"))
    return out


def served(db, co):
    return {(m.fiscal_period, m.metric_name): m.metric_value for m in db.query(FinancialMetric).filter_by(company_id=co.id)}


def test_versioned_snapshots_restatement_and_validation(db):
    co = Company(ticker="HISTCO", company_name="HistCo (synthetic)")
    db.add(co)
    db.flush()
    s1 = record_snapshot(db, co, rows(), basis="consolidated", source="test", source_url=None)
    assert s1.status == "promoted" and s1.changes["new"] == 8 and served(db, co)[("Q1FY27", "revenue")] == 1000

    # Identical fetch: no new snapshot, no new observations, last_seen moves.
    s1b = record_snapshot(db, co, rows(), basis="consolidated", source="test", source_url=None)
    assert s1b.id == s1.id and db.query(FinancialObservation).filter_by(company_id=co.id).count() == 8

    # Restatement of one value: history keeps both, served value updates, hash/version recorded.
    rev = {p: 1000 + i * 10 for i, (p, _) in enumerate(Q)}
    rev["Q1FY27"] = 990
    s2 = record_snapshot(db, co, rows(rev=rev), basis="consolidated", source="test", source_url=None)
    assert s2.status == "promoted" and s2.changes == {"new": 0, "restated": 1, "unchanged": 7, "dropped": 0}
    h = history(db, co.id, "revenue", "Q1FY27")
    assert [x["value"] for x in h] == [1000, 990] and [x["current"] for x in h] == [False, True]
    assert h[1]["supersedes"] == h[0]["observation_id"] and h[1]["extraction_version"] == "screener-parser-v1"
    assert h[0]["source_hash"] != h[1]["source_hash"]
    assert served(db, co)[("Q1FY27", "revenue")] == 990

    # Rejected: a partial page (history shrank), a basis switch, impossible values, mass change. Served data untouched.
    before = served(db, co)
    bad = [
        record_snapshot(db, co, rows(quarters=Q[:1]), basis="consolidated", source="test", source_url=None),
        record_snapshot(db, co, rows(rev=rev), basis="standalone", source="test", source_url=None),
        record_snapshot(db, co, rows(rev={**rev, "Q2FY27": -5}), basis="consolidated", source="test", source_url=None),
    ]
    assert all(s.status == "rejected" for s in bad)
    assert "shrank" in bad[0].validation["errors"][0] and "basis changed" in bad[1].validation["errors"][0]
    assert "Negative" in bad[2].validation["errors"][0]
    assert served(db, co) == before

    # Rolling window: the oldest quarter disappears and a new one appears — the old value is kept, not deleted.
    rolled = Q[:3] + [("Q3FY27", date(2026, 12, 31))]
    r2 = {p: 1000 for p, _ in rolled}
    r2["Q1FY27"] = 990
    r2.update({"Q2FY27": 1010, "Q4FY26": 1020})
    p2 = {p: 100 for p, _ in rolled}
    p2.update({"Q1FY27": 100, "Q2FY27": 101, "Q4FY26": 102})
    s3 = record_snapshot(db, co, rows(rev=r2, pat=p2, quarters=rolled), basis="consolidated", source="test", source_url=None)
    assert s3.status == "promoted" and s3.changes["dropped"] == 2 and ("Q3FY26", "revenue") in served(db, co)
    assert db.query(FinancialSnapshot).filter_by(company_id=co.id).count() == 6


def test_mass_change_is_rejected_as_parse_error(db):
    co = Company(ticker="HISTCO2", company_name="HistCo2 (synthetic)")
    db.add(co)
    db.flush()
    qs = [(f"Q{q}FY{y}", date(2000 + y, 3, 31)) for y in (25, 26) for q in (1, 2, 3, 4)]
    record_snapshot(db, co, rows(quarters=qs), basis="consolidated", source="test", source_url=None)
    s = record_snapshot(db, co, rows(rev={p: 1000 * 100 for p, _ in qs}, pat={p: 100 * 100 for p, _ in qs}, quarters=qs),
                        basis="consolidated", source="test", source_url=None)   # e.g. lakh vs crore mix-up
    assert s.status == "rejected" and "parsing or basis error" in s.validation["errors"][0]


def test_half_year_periods_are_valid(db):
    """Regression: the source emits half-year columns (H1FY27); a live refresh rejected all 23 snapshots over this."""
    co = Company(ticker="HISTCO3", company_name="HistCo3 (synthetic)")
    db.add(co)
    db.flush()
    rs = rows() + [Candidate("H1FY27", date(2026, 9, 30), "half", "cfo", 500, "crore")]
    s = record_snapshot(db, co, rs, basis="consolidated", source="test", source_url=None)
    assert s.status == "promoted", s.validation
