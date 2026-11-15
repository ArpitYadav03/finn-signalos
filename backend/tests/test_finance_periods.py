from datetime import date

import pytest

from app.services import finance as f
from app.services.periods import FiscalQuarter, fiscal_year_of, parse_fy_token, parse_month_year, quarter_of


def test_growth_basic():
    assert f.growth_pct(112, 100).value == pytest.approx(12.0)
    assert f.qoq_pct(90, 100).value == pytest.approx(-10.0)


def test_growth_zero_negative_missing():
    assert f.growth_pct(10, 0).value is None and f.growth_pct(10, 0).note == "zero base"
    assert f.growth_pct(None, 5).note == "missing value"
    # Loss narrowing from -100 to -50 is an improvement of +50% (abs base)
    assert f.yoy_pct(-50, -100).value == pytest.approx(50.0)
    # Profit/loss crossings are not expressed as a percentage
    c = f.yoy_pct(20, -10)
    assert c.value is None and "turned profitable" in c.note
    assert "turned loss-making" in f.yoy_pct(-20, 10).note


def test_margin_fcf_ratio():
    assert f.margin_pct(25, 100).value == pytest.approx(25.0)
    assert f.margin_pct(25, 0).value is None
    assert f.margin_pct(25, -5).note == "non-positive revenue"
    assert f.free_cash_flow(100, 30).value == 70
    assert f.free_cash_flow(100, -30).value == 70  # capex given as negative cash-flow line
    assert f.debt_to_equity(50, -10).note == "negative net worth"
    assert f.cash_conversion(80, 100).value == pytest.approx(0.8)
    assert f.cash_conversion(80, -5).value is None


def test_market_calcs():
    assert f.daily_return_pct(104, 100).value == pytest.approx(4.0)
    assert f.daily_return_pct(104, 0).value is None
    hist = [100.0] * 20
    assert f.volume_ratio(250, hist).value == pytest.approx(2.5)
    assert f.volume_ratio(250, [100.0] * 5).value is None  # insufficient history
    assert f.volume_ratio(250, [100.0] * 18 + [None, None]).value == pytest.approx(2.5)


def test_units_and_cagr():
    assert f.to_crore(150, "lakh") == pytest.approx(1.5)
    assert f.to_crore(42646.09, "million") == pytest.approx(4264.609)
    assert f.to_crore(160000, "inr") == pytest.approx(0.016)
    with pytest.raises(ValueError):
        f.to_crore(1, "furlongs")
    assert f.cagr_pct(100, 161.051, 5).value == pytest.approx(10.0, abs=1e-3)
    assert f.cagr_pct(-1, 10, 3).value is None


def test_fiscal_periods():
    assert fiscal_year_of(date(2026, 4, 1)) == 2027
    assert fiscal_year_of(date(2026, 3, 31)) == 2026
    assert quarter_of(date(2026, 9, 30)).label == "Q2FY27"
    assert quarter_of(date(2027, 1, 15)).label == "Q4FY27"
    assert FiscalQuarter(2027, 4).end_date == date(2027, 3, 31)
    assert FiscalQuarter(2027, 1).shift(-1).label == "Q4FY26"
    assert FiscalQuarter(2027, 2).shift(-4).label == "Q2FY26"
    assert parse_fy_token("FY27") == 2027 and parse_fy_token("FY 2027") == 2027
    assert parse_fy_token("2026-27") == 2027
    assert parse_fy_token("next year", reference=date(2026, 10, 8)) == 2028
    assert parse_month_year("Sep 2026") == date(2026, 9, 30)
