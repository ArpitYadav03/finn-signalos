"""'Next evidence to watch': the next filing that would resolve or advance an event, derived from facts FINN already
extracted — never guessed. Each milestone says what to look for, why (the filing text or rule that implies it) and,
where a rule fixes it, when it is due. A milestone is a suggestion to monitor; it is not an alert until the user turns
it into one.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.services.periods import FiscalQuarter, quarter_of

ALERT_CATEGORY = {"shareholder_approval": ["postal_ballot", "agm_egm"], "allotment": ["fund_raise"], "completion": ["fund_raise"],
                  "results": ["results"], "regulatory_approval": ["management_change"]}


def results_due(published: date) -> dict:
    """SEBI LODR Reg. 33: quarterly results within 45 days of quarter end; Q4/annual within 60 days."""
    q = quarter_of(published)                      # quarter in progress when the filing was made
    end = q.end_date
    days = 60 if q.q == 4 else 45
    return {"label": q.label, "expected_by": (end + timedelta(days=days)).isoformat(),
            "basis": f"SEBI LODR Regulation 33: quarterly results are due within {days} days of the quarter ending {end.isoformat()}"}


def next_evidence(category: str, facts: dict, published: date) -> list[dict]:
    out: list[dict] = []
    fe = facts.get("financial_event") or {}
    if fe and fe.get("action") == "issuance":
        stage = fe.get("stage")
        ev = (fe.get("evidence") or {}).get("stage")
        if stage == "board_approval":
            if facts.get("shareholder_approval_pending") or "shareholder" in (ev or "").lower():
                out.append({"kind": "shareholder_approval", "label": "Shareholder approval of the issue", "basis": f"Filing: “…{ev}…”"})
            out.append({"kind": "allotment", "label": "Allotment of the securities", "basis": "Approval authorises an issue; allotment shows it happened"})
        elif stage in ("opened",):
            out.append({"kind": "allotment", "label": "Closure and allotment of the issue", "basis": f"Filing: “…{ev}…”" if ev else "Issue opened"})
        elif stage == "allotment" and fe.get("instrument") == "warrants":
            out.append({"kind": "completion", "label": "Conversion of warrants into shares (balance payment)",
                        "expected_by": (published + timedelta(days=548)).isoformat(),
                        "basis": "Warrants convert within 18 months of allotment (SEBI ICDR Reg. 162); until then only the upfront amount is received"})
    if category == "management_change":
        if facts.get("shareholder_approval_pending"):
            out.append({"kind": "shareholder_approval", "label": f"Shareholder approval of {', '.join((facts.get('people') or ['the appointment'])[:1])}",
                        "basis": "The filing says the appointment is subject to shareholders' approval"})
        if facts.get("regulator_approval_pending"):
            out.append({"kind": "regulatory_approval", "label": "Regulator's approval", "basis": "The filing says regulatory approval is pending"})
    if category in ("results", "earnings_call"):
        nxt = results_due(published)   # results filed in Nov report Q2; the next due are for the quarter now in progress
        out.append({"kind": "results", "label": f"Next quarterly results ({nxt['label']})", "expected_by": nxt["expected_by"], "basis": nxt["basis"]})
    for m in out:
        m["alert_categories"] = ALERT_CATEGORY.get(m["kind"], [])
        m.setdefault("expected_by", None)
    return out


def resolved_by(milestone_kind: str, later_facts: list[dict], later_categories: list[str]) -> bool:
    """A later filing in the same event resolves the milestone when it reaches that stage."""
    for f, cat in zip(later_facts, later_categories):
        st = (f.get("financial_event") or {}).get("stage")
        if milestone_kind == "allotment" and st in ("allotment", "completed"):
            return True
        if milestone_kind == "completion" and st == "completed":
            return True
        if milestone_kind == "shareholder_approval" and (st == "shareholder_approval" or cat in ("postal_ballot", "agm_egm")):
            return True
        if milestone_kind == "results" and cat == "results":
            return True
    return False


_ = (date, FiscalQuarter)
