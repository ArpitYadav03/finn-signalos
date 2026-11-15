

def test_cash_conversion_difference_median():
    from app.services import finance as f

    assert round(f.cash_conversion_ebitda_pct(630, 1000).value, 2) == 63.0
    assert f.cash_conversion_ebitda_pct(100, 0).value is None and f.cash_conversion_ebitda_pct(100, -5).note == "operating profit non-positive"
    assert f.cash_conversion_ebitda_pct(None, 10).value is None
    assert f.cash_conversion_ebitda_pct(-50, 100).value == -50.0            # negative CFO is a real (bad) outcome, not missing
    assert f.difference(50, 47).value == 3 and f.difference(None, 1).value is None
    assert f.median([88, 63, 78]).value == 78 and f.median([1, None, 3, 2, 4]).value == 2.5 and f.median([None]).value is None
