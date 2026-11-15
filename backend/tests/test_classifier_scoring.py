import json
from pathlib import Path

import pytest

from app.domain.categories import Category as C
from app.services.classifier import classify
from app.services.scoring import score_event

LABELLED = json.loads((Path(__file__).resolve().parents[2] / "evals" / "datasets" / "classification.json").read_text())


@pytest.mark.parametrize("row", LABELLED, ids=lambda r: r["title"][:40])
def test_labelled_titles(row):
    assert classify(row["title"], row.get("summary")).category.value == row["expected"]


def test_ambiguous_board_outcome_uses_summary():
    c = classify("Board Meeting Outcome for Outcome Of The Meeting Of PI 2026- Special Committee",
                 "Approved issue of 5.74 crore warrants to Bajaj Finserv at ₹1,010 each")
    assert c.category == C.FUND_RAISE


def test_parent_results_not_own_results():
    assert classify("Reference To The Financial Results Issued By Suzuki Motor Corporation").category == C.PRESS_RELEASE


def test_protected_category_floor():
    s = score_event(C.LITIGATION, "dispute", max_amount_cr=0.016, annual_revenue_cr=170000)
    assert s.level == "medium"  # tiny amount lowers it, but litigation is never hidden
    assert any("Protected" in r["label"] for r in s.reasons)


def test_size_relative_materiality():
    small = score_event(C.MA, "acquisition", max_amount_cr=5, annual_revenue_cr=100000)
    big = score_event(C.MA, "acquisition", max_amount_cr=20000, annual_revenue_cr=100000)
    assert big.score > small.score


def test_intimation_and_novelty_lower_score():
    base = score_event(C.RESULTS, "Financial results for the quarter")
    intim = score_event(C.RESULTS, "Board meeting intimation for financial results")
    repeat = score_event(C.RESULTS, "Financial results for the quarter", is_novel=False)
    assert intim.score < base.score and repeat.score < base.score


def test_llm_adjustment_is_bounded():
    s = score_event(C.UNCLASSIFIED, "esop allotment", llm_adjustment=90, llm_reason="hype")
    assert s.score <= 4 + 10


def test_cfo_change_ranks_high():
    s = score_event(C.MANAGEMENT_CHANGE, "Resignation of Chief Financial Officer")
    assert s.level == "high"
