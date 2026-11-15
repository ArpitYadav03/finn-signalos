"""Explainable materiality scoring.

score = category baseline + content modifiers + novelty + market context + user preference (+ bounded LLM adjust)

Every component is recorded as a {label, points} reason so the UI can show *why* something ranks where it
does. An LLM can nudge the score by at most ±10 and can never push a protected category below "medium".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.categories import PROTECTED_CATEGORIES, Category

C = Category

BASELINE: dict[Category, int] = {
    C.RESULTS: 70,
    C.MA: 72,
    C.MANAGEMENT_CHANGE: 55,
    C.LITIGATION: 55,
    C.FUND_RAISE: 58,
    C.CREDIT_RATING: 45,
    C.PLEDGING: 55,
    C.INSIDER_TRADING: 35,
    C.CAPEX: 52,
    C.DIVIDEND: 32,
    C.BONUS_SPLIT: 45,
    C.PRESS_RELEASE: 28,
    C.EARNINGS_CALL: 22,
    C.INVESTOR_MEETINGS: 10,
    C.AGM_EGM: 14,
    C.POSTAL_BALLOT: 14,
    C.UNCLASSIFIED: 4,
}

# (pattern, points, label) applied to title + summary + extracted text
MODIFIERS: list[tuple[str, int, str]] = [
    (r"\b(ceo|cfo|managing director|md|chief executive|chief financial)\b", 18, "Top-level executive involved"),
    (r"resign|cessation|stepping down|steps down", 6, "Departure rather than routine appointment"),
    (r"downgrad|negative outlook|rating watch with negative|suspended", 20, "Adverse rating action"),
    (r"upgrad|positive outlook", 8, "Rating upgrade"),
    (r"reaffirm", -12, "Rating merely reaffirmed"),
    (r"penalt|show cause|demand|search|seizure|fraud|forensic|insolvency|nclt", 10, "Regulatory/legal exposure"),
    (r"invocation|invoked", 15, "Pledge invocation"),
    (r"\b(qip|rights issue|preferential)\b", 6, "Equity dilution possible"),
    (r"buy-?back", 6, "Capital return via buyback"),
    (r"special dividend|interim dividend", 4, "Dividend declared"),
    (r"(order|contract)[^.]{0,60}(worth|valued|value of|aggregating)", 12, "Order with disclosed value"),
    (r"intimation|prior notice|notice of board meeting", -18, "Advance notice only — outcome comes later"),
    (r"transcript", 10, "Transcript available — management commentary to review"),
    (r"clarification", 8, "Clarification on market rumour/news"),
    (r"newspaper|advertisement", -10, "Publication copy of an earlier filing"),
]
_MODS = [(re.compile(p, re.I), pts, label) for p, pts, label in MODIFIERS]


@dataclass
class ScoreResult:
    score: float
    level: str
    reasons: list[dict] = field(default_factory=list)


SIZE_SENSITIVE = {C.LITIGATION, C.MA, C.FUND_RAISE, C.CAPEX, C.PRESS_RELEASE}


def level_for(score: float) -> str:
    if score >= 65:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 18:
        return "low"
    return "routine"


def score_event(
    category: Category,
    text: str,
    *,
    is_novel: bool = True,
    same_day_price_move_pct: float | None = None,
    same_day_volume_ratio: float | None = None,
    user_watched_categories: set[str] | None = None,
    structured_mods: list[tuple[str, int]] | None = None,
    max_amount_cr: float | None = None,
    annual_revenue_cr: float | None = None,
    llm_adjustment: float | None = None,
    llm_reason: str | None = None,
    price_threshold_pct: float = 4.0,
    volume_threshold: float = 2.0,
) -> ScoreResult:
    reasons: list[dict] = [{"label": f"Category baseline: {category.value}", "points": BASELINE[category]}]
    score = float(BASELINE[category])

    structured = bool(structured_mods)
    if category != C.UNCLASSIFIED:
        for rx, pts, label in _MODS:
            if rx.search(text or ""):
                # Executive keywords only matter for management changes; avoid boosting e.g. "CFO signed results".
                if label == "Top-level executive involved" and category != C.MANAGEMENT_CHANGE:
                    continue
                # Leadership events are scored from extracted facts (role seniority, continuity vs exit), not keywords.
                if structured and label in ("Top-level executive involved", "Departure rather than routine appointment"):
                    continue
                # An outcome/result is the event itself, not advance notice of it.
                if label.startswith("Advance notice") and re.search(r"outcome|approved|voting|scrutini[sz]er", text or "", re.I):
                    continue
                # Extracted facts beat title wording: "Intimation of repayment" can report a completed repayment.
                if label.startswith("Advance notice") and any("completed" in l.lower() for l, _ in structured_mods or []):
                    continue
                if label.startswith("Rating") and category != C.CREDIT_RATING:
                    continue
                score += pts
                reasons.append({"label": label, "points": pts})

    # Size matters: a ₹1.6 lakh penalty is not material for a ₹1.7 lakh-crore company.
    if max_amount_cr is not None and annual_revenue_cr and annual_revenue_cr > 0 and category in SIZE_SENSITIVE:
        share = max_amount_cr / annual_revenue_cr * 100
        if share < 0.2:
            score -= 25
            reasons.append({"label": f"Amount small vs size (₹{max_amount_cr:,.2f} cr = {share:.3f}% of revenue)", "points": -25})
        elif share >= 5:
            score += 10
            reasons.append({"label": f"Amount large vs size ({share:.1f}% of annual revenue)", "points": 10})

    for label, pts in structured_mods or []:
        score += pts
        reasons.append({"label": label, "points": pts})

    if not is_novel:
        score -= 15
        reasons.append({"label": "Another filing about an event already surfaced (grouped)", "points": -15})

    if same_day_price_move_pct is not None and abs(same_day_price_move_pct) >= price_threshold_pct:
        score += 10
        reasons.append({"label": f"Stock moved {same_day_price_move_pct:+.1f}% in the first full session after the filing (timing, not cause)", "points": 10})
    if same_day_volume_ratio is not None and same_day_volume_ratio >= volume_threshold:
        score += 6
        reasons.append({"label": f"Volume {same_day_volume_ratio:.1f}× 20-day average", "points": 6})

    if user_watched_categories and category.value in user_watched_categories:
        score += 8
        reasons.append({"label": "Category you follow", "points": 8})

    if llm_adjustment:
        adj = max(-10.0, min(10.0, llm_adjustment))
        score += adj
        reasons.append({"label": f"AI materiality read: {llm_reason or 'content-based'}", "points": round(adj, 1)})

    score = max(0.0, min(100.0, score))
    # Guardrail: protected categories never fall below "medium" because of modifiers/LLM.
    routine_servicing = any(l.startswith("Routine debt repayment") for l, _ in structured_mods or [])
    if category in PROTECTED_CATEGORIES and score < 40 and is_novel and not routine_servicing:
        # The floor protects capital raises from being hidden; routine debt repayment at maturity is not a raise.
        reasons.append({"label": "Protected category floor (never hidden)", "points": round(40 - score, 1)})
        score = 40.0
    return ScoreResult(round(score, 1), level_for(score), reasons)
