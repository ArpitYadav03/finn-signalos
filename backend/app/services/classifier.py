"""Rule-based announcement classifier (stage 1 of the hybrid classifier).

Exchange filing titles are highly formulaic ("Board Meeting Outcome for ...", "Change in Directorate"),
so ordered regex rules get most filings right for free. Only low-confidence results are escalated to an
LLM (see agents/filing_agent.py) — that is the main cost lever on the Push side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.categories import Category

C = Category

# (category, pattern, confidence). Order matters: first match wins, so specific rules precede generic ones.
_RULES: list[tuple[Category, str, float]] = [
    # Routine paperwork that often *mentions* important words (e.g. "newspaper publication of results").
    (C.UNCLASSIFIED, r"newspaper (publication|advertisement)|trading window|loss of share certificate|duplicate share|"
                     r"reg(ulation)?\.?\s*74\s*\(5\)|certificate under|compliance certificate|investor complaints|"
                     r"\besop\b|\besos\b|employee stock option|allotment of equity shares under|"
                     r"\bbrsr\b|business responsibility|esg|change in (registrar|rta)|"
                     r"intimation of (book closure)?\s*record date for (interest|redemption)|"
                     r"statement of deviation|non[- ]applicability|closure of trading|"
                     r"(statutory|secretarial|internal|cost) auditors?", 0.85),
    (C.EARNINGS_CALL, r"earnings? (conference )?call|conference call|con[- ]?call|audio recording|"
                      r"transcript|webcast|analyst call|investor call", 0.9),
    # Results announced by a parent/subsidiary are news *about* a related entity, not this company's results.
    (C.PRESS_RELEASE, r"results (issued|declared|published|announced) by|reference to the financial results|"
                      r"results of (the )?(holding|parent|promoter|subsidiary|associate)", 0.8),
    (C.RESULTS, r"financial results?|\bresults?\b for the (quarter|year|period)|integrated filing.*financial|"
                r"quarterly results|"
                r"un-?audited|audited (standalone|consolidated)|shareholder'?s? letter|results presentation", 0.9),
    (C.PLEDGING, r"pledg|encumbrance|reg(ulation)?\.?\s*31\s*\(1\)", 0.9),
    (C.INSIDER_TRADING, r"insider trading|reg(ulation)?\.?\s*7\s*\(2\)|reg(ulation)?\.?\s*29\s*\(|\bsast\b|"
                        r"disclosure of (acquisition|disposal)|promoter (group )?(purchase|sale)", 0.85),
    (C.MANAGEMENT_CHANGE, r"change in (directorate|management|key managerial|kmp|senior management)|"
                          r"resignation|appointment of|re-?appointment|cessation|retire|"
                          r"\b(ceo|cfo|coo|managing director|whole[- ]time director|company secretary|"
                          r"independent director|chairman|chairperson)\b", 0.85),
    (C.CREDIT_RATING, r"credit rating|\brating\b.*(crisil|icra|care|india ratings|fitch|moody|s&p)|"
                      r"(crisil|icra|care ratings|india ratings)", 0.9),
    (C.MA, r"acqui(sition|re)|amalgamation|merger|demerger|scheme of arrangement|joint venture|\bjv\b|"
           r"takeover|open offer|divest|sale of (stake|shares|business|undertaking)|"
           r"investment in (wholly|subsidiary|step)|incorporation of (a )?(wholly|subsidiary|step)|slump sale", 0.85),
    (C.FUND_RAISE, r"\bqip\b|qualified institution|preferential (issue|allotment)|rights issue|buy-?back|"
                   r"fund raising|raising of funds|non-convertible debenture|\bncds?\b|commercial paper|"
                   r"issue of (bonds|debentures|warrants)|warrants", 0.85),
    (C.BONUS_SPLIT, r"\bbonus\b|stock split|sub-?division|split of (equity )?shares", 0.9),
    (C.DIVIDEND, r"dividend", 0.9),
    (C.LITIGATION, r"litigation|dispute|penalt|show cause|\bscn\b|demand (order|notice)|tax demand|"
                   r"\bgst\b.*(order|demand|notice)|regulatory action|sebi order|court|tribunal|nclt|"
                   r"insolvency|arbitration|fine imposed|search (and|&) seizure|raid|"
                   r"(order|notice) (passed|received) (by|from) .*(authority|commissioner|department)", 0.8),
    (C.CAPEX, r"capex|capital expenditure|capacity (expansion|addition)|new (plant|facility|unit)|"
              r"greenfield|brownfield|commission(ing|ed) of|expansion project", 0.85),
    (C.POSTAL_BALLOT, r"postal ballot|e-?voting", 0.9),
    (C.AGM_EGM, r"\bagm\b|\begm\b|annual general meeting|extra-?ordinary general meeting|general meeting|"
                r"shareholders? meeting", 0.85),
    (C.INVESTOR_MEETINGS, r"analyst\s*/\s*investor meet|investor meet|analyst meet|institutional investor|"
                          r"investor conference|roadshow|non-deal", 0.9),
    (C.PRESS_RELEASE, r"press release|media release|order (win|received|bagged)|bagging|receipt of (an )?order|"
                      r"letter of (award|intent)|launch|partnership|collaboration|clarification|"
                      r"\bcontract\b|memorandum of understanding|\bmou\b|award", 0.75),
    (C.RESULTS, r"\bboard meeting\b|outcome of (the )?(board )?meeting", 0.6),  # often results; confirmed via summary below
]

_COMPILED = [(cat, re.compile(p, re.I), conf) for cat, p, conf in _RULES]


@dataclass(frozen=True)
class Classification:
    category: Category
    confidence: float
    matched_rule: str | None
    method: str = "rules-v1"


def _match(text: str) -> tuple[Category, float, str] | None:
    for cat, rx, conf in _COMPILED:
        if rx.search(text):
            return cat, conf, rx.pattern[:60]
    return None


def classify(title: str, summary: str | None = None) -> Classification:
    """Classify on the title first (most reliable). If the title is ambiguous (e.g. 'Board Meeting Outcome'),
    let the exchange listing's one-line subject decide, at slightly reduced confidence."""
    t = _match(title or "")
    s = _match(summary or "") if summary else None
    if t and t[1] >= 0.7:
        return Classification(t[0], t[1], t[2])
    if s and s[0] != C.UNCLASSIFIED and (t is None or s[1] - 0.1 > t[1]):
        return Classification(s[0], round(s[1] - 0.1, 2), s[2])
    if t:
        return Classification(t[0], t[1], t[2])
    if s:
        return Classification(s[0], round(s[1] - 0.2, 2), s[2])
    return Classification(C.UNCLASSIFIED, 0.3, None)


def is_intimation(title: str) -> bool:
    """Prior-notice filings (e.g. 'board meeting intimation') are less material than the outcome itself."""
    return bool(re.search(r"intimation|notice of|prior intimation", title or "", re.I)) and not re.search(
        r"outcome", title or "", re.I
    )
