"""Event timing vs trading sessions, and honest coverage of a limited latest-filings source. SYNTHETIC data."""
from datetime import date, datetime, timedelta

from app.models import Company, Price, SourceCoverage
from app.services.market import event_timing


def ist(y, m, d, hh, mm):   # IST wall time -> naive UTC (how the DB stores publication times)
    return datetime(y, m, d, hh, mm) - timedelta(hours=5, minutes=30)


def _company(db):
    co = Company(ticker="TIMECO", company_name="TimeCo (synthetic)")
    db.add(co)
    db.flush()
    # Sessions Mon 2 Nov – Fri 6 Nov 2026 and Mon 9 Nov 2026 (weekend 7–8 Nov has no bars).
    days = [date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9)]
    for i, d in enumerate(days):
        db.add(Price(company_id=co.id, trading_date=d, open=100 + i, high=102 + i, low=99 + i, close=100 + i * 2, volume=1000, source="test"))
    db.commit()
    return co


def test_event_timing_separates_pre_event_context_from_reaction(db):
    co = _company(db)
    before = event_timing(db, co.id, ist(2026, 11, 3, 8, 0))
    assert before["relation"] == "before_open" and before["reaction"]["date"] == "2026-11-03" and before["pre_event"]["date"] == "2026-11-02"
    during = event_timing(db, co.id, ist(2026, 11, 3, 12, 30))
    assert during["relation"] == "during_session" and during["mixed_session"]["date"] == "2026-11-03"
    assert during["reaction"]["date"] == "2026-11-04"          # the split day is not presented as the reaction
    after = event_timing(db, co.id, ist(2026, 11, 3, 18, 41))
    assert after["relation"] == "after_close" and after["pre_event"]["date"] == "2026-11-03"   # that day's bar is pre-event
    assert after["reaction"]["date"] == "2026-11-04"
    weekend = event_timing(db, co.id, ist(2026, 11, 7, 11, 0))
    assert weekend["relation"] == "non_trading_day" and weekend["reaction"]["date"] == "2026-11-09"
    future = event_timing(db, co.id, ist(2026, 11, 9, 17, 0))
    assert future["reaction"] is None and "not traded yet" in future["label"]
    assert "not evidence" in future["caution"]


def test_listing_coverage_tracks_continuity_and_gaps(db):
    from app.api.routes import _window_coverage
    from app.ingestion.pipeline import record_listing_coverage

    co = Company(ticker="COVCO", company_name="CovCo (synthetic)")
    db.add(co)
    db.flush()
    d = lambda day: datetime(2026, 11, day, 6, 0)  # noqa: E731
    record_listing_coverage(db, co, [d(1), d(3), d(5)])
    cov = db.query(SourceCoverage).filter_by(company_id=co.id).one()
    assert cov.continuous_since == d(1) and cov.oldest_item_at == d(1) and cov.newest_item_at == d(5)
    record_listing_coverage(db, co, [d(4), d(6), d(8)])          # overlaps the previous listing: still continuous
    db.refresh(cov)
    assert cov.continuous_since == d(1)
    record_listing_coverage(db, co, [d(12), d(13)])              # oldest is after the previous newest: possible gap
    db.refresh(cov)
    assert cov.continuous_since == d(12)
    record_listing_coverage(db, co, [], error="index unreachable")
    db.refresh(cov)
    assert cov.last_error == "index unreachable" and cov.continuous_since == d(12)

    w = _window_coverage(db, [co], d(10), d(14))
    assert not w["complete_for_window"] and w["gaps"][0]["ticker"] == "COVCO" and w["failing"]
    w2 = _window_coverage(db, [co], d(12) + timedelta(hours=1), d(14))
    assert w2["gaps"] == [] and w2["failing"]                    # covered, but the latest check failed → not complete
    other = Company(ticker="NEWCO", company_name="NewCo (synthetic)")
    db.add(other)
    db.flush()
    assert _window_coverage(db, [co, other], d(13), d(14))["never_checked"] == ["NEWCO"]
