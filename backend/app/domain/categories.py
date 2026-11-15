"""Announcement taxonomy from the ENAM reference document, stored as a stable internal enum."""
from __future__ import annotations

from enum import Enum


class Category(str, Enum):
    RESULTS = "results"
    MA = "M&A"
    FUND_RAISE = "fund_raise"
    MANAGEMENT_CHANGE = "management_change"
    LITIGATION = "litigation"
    INSIDER_TRADING = "insider_trading"
    EARNINGS_CALL = "earnings_call"
    INVESTOR_MEETINGS = "investor_meetings"
    CREDIT_RATING = "credit_rating"
    DIVIDEND = "dividend"
    BONUS_SPLIT = "bonus_split"
    CAPEX = "capex"
    PRESS_RELEASE = "press_release"
    POSTAL_BALLOT = "postal_ballot"
    AGM_EGM = "agm_egm"
    PLEDGING = "pledging"
    UNCLASSIFIED = "unclassified"


CATEGORY_LABELS: dict[Category, str] = {
    Category.RESULTS: "Results",
    Category.MA: "M&A",
    Category.FUND_RAISE: "Fund raise",
    Category.MANAGEMENT_CHANGE: "Management change",
    Category.LITIGATION: "Litigation / regulatory",
    Category.INSIDER_TRADING: "Insider trading",
    Category.EARNINGS_CALL: "Earnings call",
    Category.INVESTOR_MEETINGS: "Investor meetings",
    Category.CREDIT_RATING: "Credit rating",
    Category.DIVIDEND: "Dividend",
    Category.BONUS_SPLIT: "Bonus / split",
    Category.CAPEX: "Capex",
    Category.PRESS_RELEASE: "Press release",
    Category.POSTAL_BALLOT: "Postal ballot",
    Category.AGM_EGM: "AGM / EGM",
    Category.PLEDGING: "Pledging",
    Category.UNCLASSIFIED: "Routine",
}

# Categories that may never be hidden/collapsed by an AI score alone (explainability guardrail).
PROTECTED_CATEGORIES = {
    Category.RESULTS,
    Category.MA,
    Category.MANAGEMENT_CHANGE,
    Category.LITIGATION,
    Category.CREDIT_RATING,
    Category.PLEDGING,
    Category.FUND_RAISE,
}
