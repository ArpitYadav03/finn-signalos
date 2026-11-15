"""Structured facts from exchange filings (deterministic).

Indian Reg-30 letters share a layout: letterhead → addressees → "Sub:" line (the event, stated exactly) → body →
"Yours faithfully" + signature block → annexures (e.g. the appointee's profile). Reading the subject and body
separately avoids classic mistakes such as taking "Company Secretary" from the signature as the appointee's role.

Facts feed three things: event deduplication (same person + same kind of change = one event), event-specific
"why it matters" text, and explainable materiality modifiers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services import finance

HONORIFIC = r"(?:Mr|Ms|Mrs|Dr|Shri|Smt|Prof)\.?"
NAME_PART = r"(?:[A-Z]\.\s?)*[A-Z][a-zA-Z’'\-]+"
PERSON_RE = re.compile(rf"\b{HONORIFIC}\s+({NAME_PART}(?:\s+{NAME_PART}){{0,3}})")
BARE_NAME_RE = re.compile(r"\b((?:[A-Z]\.\s?){1,3}[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}|[A-Z][a-z]+\s+(?:[A-Z]\.\s?)?[A-Z][a-z’']+(?:\s+[A-Z][a-z]+)?)\b")

# (pattern, canonical label, tier) — tier 1 = runs the company, 4 = administrative.
ROLES: list[tuple[str, str, int]] = [
    (r"md\s*(?:&|and)\s*ceo|managing director\s*(?:&|and)\s*(?:ceo|chief executive)", "MD & CEO", 1),
    (r"chief executive officer|\bceo\b", "CEO", 1),
    (r"managing director|\bmd\b", "Managing Director", 1),
    (r"chief financial officer|\bcfo\b", "CFO", 1),
    (r"chair(?:man|person)", "Chairperson", 1),
    (r"deputy managing director", "Deputy MD", 2),
    (r"executive director", "Executive Director", 2),
    (r"whole[- ]time director", "Whole-time Director", 2),
    (r"chief operating officer|\bcoo\b", "COO", 2),
    # Any other C-suite title, kept verbatim ("Global Chief Technical Operating Officer", "Chief Digital Officer").
    (r"(?:global |group |deputy )?chief(?: [a-z&]+){1,4} officer", "Chief Officer", 2),
    (r"group head[^,.;()]{0,60}", "Group Head", 3),
    (r"independent director", "Independent Director", 3),
    (r"non[- ]executive director", "Non-executive Director", 3),
    (r"senior management personnel", "Senior Management", 3),
    (r"key managerial personnel|\bkmp\b", "Key Managerial Personnel", 3),
    (r"company secretary|compliance officer", "Company Secretary", 4),
    (r"statutory auditor|secretarial auditor|internal auditor", "Auditor", 4),
    (r"\bdirector\b", "Director", 3),
]
ROLE_RES = [(re.compile(p, re.I), label, tier) for p, label, tier in ROLES]

ACTIONS = [
    ("reappointment", r"re-?appoint"),
    ("retirement", r"superannuat|retire(?:d|ment|s)?\b|attain(?:ed|ing) the age"),
    ("resignation", r"resign"),
    ("cessation", r"cessation|ceased? to be|completion of (?:his|her|the) term|term (?:has )?ended"),
    ("demise", r"demise|passed away|sad demise"),
    ("appointment", r"\bappoint|assume (?:office|charge)|take (?:over )?charge"),
]
DEPARTURES = {"retirement", "resignation", "cessation", "demise"}
# Sentences that recount an earlier intimation ("The Company, vide its letter … dated …, had informed …") are history:
# they must not set the current action or approval status.
HISTORY_SENT = re.compile(r"vide (?:its|our) (?:letter|intimation)|had (?:informed|intimated)|earlier (?:letter|intimation)|"
                          r"in continuation (?:of|to) our", re.I)
APPROVAL_OBTAINED = re.compile(r"(?:shareholders|members)[^.]{0,80}?(?:have|has) (?:duly )?approved|approved by (?:the )?(?:shareholders|members)|"
                               r"(?:special|ordinary) resolution[^.]{0,60}?(?:passed|deemed to have been passed)", re.I)


ABBREV_END = re.compile(r"(?:\b(?:Mr|Ms|Mrs|Dr|Shri|Smt|Prof|No|Nos|viz|Ltd|Co|St|vs|i\.e|e\.g)|\b[A-Z])\.$")


def _sentences(text: str) -> list[str]:
    """Sentence split that does not break after honorifics or initials ("Mr. R. Seshasayee")."""
    out: list[str] = []
    for part in re.split(r"(?<=[.;])\s+", re.sub(r"\s+", " ", text or "")):
        if out and ABBREV_END.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        elif part:
            out.append(part)
    return out


def current_text(text: str) -> str:
    """The letter without sentences that only recount earlier intimations."""
    return " ".join(x for x in _sentences(FORM_BOILERPLATE.sub(" ", text or "")) if not HISTORY_SENT.search(x))


def action_for(lead: str, others: list[str], text: str) -> str | None:
    """The action that applies to the lead person: departure verbs whose object is someone else ("upon completion of
    the term of Mr. R. Seshasayee") are ignored; only sentences that mention the lead count."""
    sn = surname(lead)
    other_sn = [surname(o) for o in others if surname(o) != sn]
    for name, rx in ACTIONS:
        for sent in _sentences(text):
            if sn not in sent.lower().replace("’", "'"):
                continue
            for m in re.finditer(rx, sent, re.I):
                tail = sent[m.start(): m.end() + 70].lower()
                if name in DEPARTURES and any(o in tail for o in other_sn):
                    continue
                return name
    return None
TIER_LABEL = {1: "top leadership", 2: "board-level executive", 3: "senior management / board", 4: "administrative role"}


@dataclass
class Letter:
    subject: str
    body: str
    annex: str


def split_letter(text: str) -> Letter:
    t = re.sub(r"\s+", " ", text or "").strip()
    m = re.search(r"\bSub(?:ject)?\s*[:\-–]\s*(.{10,400}?)(?=\s+(?:Ref(?:erence)?\s*[:\-–]|Dear\b|Sir\b|Madam\b|In (?:terms|furtherance|compliance|continuation))|$)", t, re.I)
    subject = m.group(1).strip(" .") if m else ""
    start = m.end() if m else 0
    end_m = re.search(r"\b(Yours (?:faithfully|sincerely|truly)|Thanking you|For and on behalf of)\b", t[start:], re.I)
    body = t[start: start + end_m.start()] if end_m else t[start:]
    annex = t[start + end_m.end():] if end_m else ""
    return Letter(subject, body.strip(), annex.strip())


# Capitalised words that start a form field or sentence, not a surname: Reg-30 disclosure tables put "Name Mr X Reason
# for change ..." on one line, and the name pattern would otherwise read "X Reason" as the person.
NOT_NAME = {"reason", "name", "designation", "date", "appointment", "re-appointment", "resignation", "cessation", "brief",
            "profile", "details", "term", "effective", "disclosure", "relationship", "director", "the", "of", "has", "is", "as",
            "was", "will", "and", "from", "with", "company", "limited", "ltd", "dear", "sir", "madam", "yours"}


def _trim_name(n: str) -> str:
    words = n.split()
    for i, w in enumerate(words):
        if i > 0 and w.lower().strip(".,") in NOT_NAME:
            return " ".join(words[:i])
    return n


def _dedupe_names(names: list[str]) -> list[str]:
    clean = []
    for n in names:
        n = _trim_name(re.sub(r"\s+", " ", n).strip(" ,."))
        if len(n) < 4:
            continue
        clean.append(n)
    # Drop names contained in a longer one ("Rangan" within "V. Srinivasa Rangan") and keep first-seen order.
    out: list[str] = []
    for n in sorted(dict.fromkeys(clean), key=len, reverse=True):
        if not any(n in o for o in out):
            out.append(n)
    order = {n: i for i, n in enumerate(clean)}
    return sorted(out, key=lambda n: order.get(n, 99))


def people(*texts: str) -> list[str]:
    found: list[str] = []
    for t in texts:
        found += [m.group(1) for m in PERSON_RE.finditer(t or "")]
    return _dedupe_names(found)[:4]


def surname(name: str) -> str:
    return re.split(r"\s+", name.strip())[-1].lower().replace("’", "'")


def role_near(name: str, *texts: str) -> tuple[str | None, int | None]:
    """Most senior role mentioned in sentences that mention this person (subject first, then body)."""
    sn = surname(name)
    best: tuple[str | None, int | None] = (None, None)
    for t in texts:
        for sent in re.split(r"(?<=[.;])\s+", t or ""):
            if sn not in sent.lower().replace("’", "'"):
                continue
            for rx, label, tier in ROLE_RES:
                m = rx.search(sent)
                if m and (best[1] is None or tier < best[1]):
                    lab = label
                    if label == "Chief Officer":
                        lab = " ".join(w if w in ("&",) else w.capitalize() for w in m.group(0).split())
                    elif label == "Group Head":
                        lab = re.sub(r"\s+(and|&)\s+a\s+.*$", "", m.group(0).strip(" –-")).title().replace("Sme", "SME")
                    best = (lab, tier)
        if best[0]:
            return best
    return best


# SEBI's Reg-30 disclosure table lists every possible reason as a field label ("Reason for change viz. appointment,
# resignation, removal, death or otherwise"); that label says nothing about this filing's action.
FORM_BOILERPLATE = re.compile(r"reason for change\s*(?:viz\.?|i\.e\.?|namely)?[^.]{0,40}?appointment,\s*resignation,\s*removal,\s*death\s*or\s*otherwise", re.I)


def action_of(*texts: str) -> str | None:
    for t in texts:
        t = FORM_BOILERPLATE.sub(" ", t or "")
        for name, rx in ACTIONS:
            if re.search(rx, t, re.I):
                return name
    return None


@dataclass
class MgmtFacts:
    people: list[str] = field(default_factory=list)
    role: str | None = None
    tier: int | None = None
    action: str | None = None
    effective: str | None = None
    term: str | None = None
    regulator_approval: str | None = None
    shareholder_approval_pending: bool = False
    shareholder_approval_obtained: bool = False
    approved_role: str | None = None
    immediate: bool = False
    reason: str | None = None
    tenure_years: int | None = None
    age: int | None = None
    profile: str | None = None
    subject: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], False)}


def management_facts(text: str, listing: str = "", title: str = "") -> MgmtFacts:
    L = split_letter(text)
    f = MgmtFacts(subject=L.subject or None)
    bare = [m.group(1) for m in re.finditer(
        r"approv\w*\s+(?:the\s+)?(?:re-?appointment of\s+|appointment of\s+)?((?:[A-Z]\.\s?)*[A-Z][a-z]+(?:\s+[A-Z][a-z’']+){1,2})\s+as\b",
        f"{L.subject} {listing}")]
    f.people = _dedupe_names(people(L.subject, L.body, listing) + people(L.annex) + bare)[:5]
    f.action = action_of(L.subject, listing, L.body, title)
    current = current_text(L.body)
    if f.people:
        # Letters can name several people (scrutiniser, signatory, appointee): lead with the one tied to the most
        # senior role, preferring people named in the subject or listing subject.
        ranked = []
        for i, n in enumerate(f.people):
            role, tier = role_near(n, L.subject, L.body, listing, L.annex)
            in_subject = surname(n) in f"{L.subject} {listing}".lower().replace("’", "'")
            ranked.append(((0 if in_subject else 1), tier or 9, i, n, role, tier))
        ranked.sort()
        _, _, _, lead, f.role, f.tier = ranked[0]
        f.people = [lead] + [r[3] for r in ranked[1:]]
    if f.role is None:
        # e.g. a postal-ballot result: "approved Anup Bagchi as Director and MD & CEO"
        for rx, label, tier in ROLE_RES:
            if rx.search(f"{L.subject} {listing}"):
                f.role, f.tier = label, tier
                break
    body = f"{L.subject} {L.body}"
    m = re.search(r"(?:with effect from|effective from|w\.e\.f\.?)\s+(?:the close of business hours on\s+)?([A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]+,? \d{4})", body)
    f.effective = m.group(1) if m else None
    m = re.search(r"for a (?:period|term) of\s+(\d+)\s*(?:\(\w+\))?\s*(year|month)s?", body, re.I)
    f.term = f"{m.group(1)} {m.group(2).lower()}{'s' if m.group(1) != '1' else ''}" if m else None
    if re.search(r"approval (?:of|from) the reserve bank|rbi[^.]{0,60}approv|approv[^.]{0,40}\brbi\b", body, re.I):
        f.regulator_approval = "RBI"
    elif re.search(r"approval (?:of|from) (?:the )?(?:irdai|sebi)", body, re.I):
        f.regulator_approval = re.search(r"(irdai|sebi)", body, re.I).group(1).upper()
    # Approval obtained in this letter overrides a pending approval quoted from an earlier intimation.
    cur_body = f"{L.subject} {current}"
    f.shareholder_approval_obtained = bool(APPROVAL_OBTAINED.search(cur_body))
    f.shareholder_approval_pending = (not f.shareholder_approval_obtained and
                                      bool(re.search(r"subject to (?:the )?approval of (?:the )?(?:shareholders|members)", cur_body, re.I)))
    if f.people and not action_of(L.subject, listing):
        # No action in the subject or listing line: take it from the current letter, for the lead person only.
        f.action = action_for(f.people[0], f.people[1:], f"{L.subject} {current}") or f.action
    if f.shareholder_approval_obtained:
        m = next((x for x in _sentences(cur_body) if APPROVAL_OBTAINED.search(x)), "")
        f.approved_role = next((label for rx, label, _ in ROLE_RES if rx.search(m)), None)
    f.immediate = bool(re.search(r"with immediate effect", body, re.I))
    m = re.search(r"(personal reasons|other professional opportunit\w+|health reasons|pursue (?:other|new) opportunit\w+)", body, re.I)
    f.reason = m.group(1).lower() if m else None
    m = re.search(r"(?:nearly|about|over|more than|around)?\s*(\d{1,2})\s+years? (?:of (?:service|association)|with the (?:bank|company))|association with the (?:bank|company) for (?:nearly|about|over)?\s*(\d{1,2}) years", body, re.I)
    if m:
        f.tenure_years = int(m.group(1) or m.group(2))
    m = re.search(r"aged\s+(?:[a-z\-]+\s+)?\(?(\d{2})\)?\s+years", L.annex + " " + body, re.I)
    f.age = int(m.group(1)) if m else None
    pi = re.search(r"profile", L.annex, re.I)
    if pi:  # first sentence-cased "Mr./Ms. Name ..." after the PROFILE heading, up to ~600 chars
        m = re.search(r"\b((?:Mr|Ms|Mrs|Dr)\. (?:[A-Z]\. ?)*[A-Z][a-z].{60,600}?\.)(?=\s|$)", L.annex[pi.end():])
        f.profile = m.group(1).strip() if m else None
    if not f.people:
        # Shareholder-vote summaries often omit honorifics: "approved Anup Bagchi as Director and MD & CEO"
        m = re.search(r"approv\w*\s+(?:the\s+)?(?:re-?appointment of\s+|appointment of\s+)?((?:[A-Z]\.\s?)*[A-Z][a-z]+(?:\s+[A-Z][a-z’']+){1,2})\s+as\b",
                      f"{L.subject} {listing}")
        if m:
            f.people = [m.group(1)]
            f.action = f.action or "appointment"
            f.role, f.tier = role_near(m.group(1), listing, L.subject) if f.role is None else (f.role, f.tier)
    return f


def is_continuity(f: MgmtFacts) -> bool:
    return f.action == "reappointment"


def management_assessment(f: MgmtFacts) -> tuple[list[tuple[str, int]], str]:
    """Explainable modifiers + a 'why it matters' grounded in the extracted facts."""
    who = f.people[0] if f.people else "The executive"
    role = f.role or "their role"
    tier = f.tier or 3
    mods: list[tuple[str, int]] = []
    parts: list[str] = []
    if f.action == "reappointment":
        mods.append(("Continuity: incumbent reappointed, no change in who leads", -20))
        detail = [x for x in (f"for {f.term}" if f.term else None, f"from {f.effective}" if f.effective else None) if x]
        parts.append(f"Leadership continuity, not disruption: {who} stays on as {role}" + (f" {' '.join(detail)}" if detail else "") + ".")
        if f.regulator_approval:
            parts.append(f"{f.regulator_approval} has approved the reappointment" + ("; shareholder approval is still pending." if f.shareholder_approval_pending else "."))
            mods.append((f"{f.regulator_approval} approval obtained (regulatory step cleared)", 0))
        elif f.shareholder_approval_pending:
            parts.append("Shareholder approval is still pending.")
        if f.term and re.match(r"1 year|[1-9] months", f.term):
            mods.append(("Short (≤1-year) term: succession may follow", 4))
            parts.append("A short extension can precede a succession — watch later filings for a named successor.")
    elif f.action in ("resignation", "cessation", "demise"):
        pts = {1: 28, 2: 16, 3: 6, 4: -10}[tier]
        mods.append((f"Unplanned departure ({TIER_LABEL[tier]})", pts))
        if f.immediate:
            mods.append(("With immediate effect", 10))
        parts.append(f"Leadership disruption: {who} is leaving as {role}" + (" with immediate effect" if f.immediate else "")
                     + (f", citing {f.reason}" if f.reason else "") + ".")
        parts.append("Check who covers the role in the interim and whether a successor is named." if tier <= 2
                     else "Below board level: limited disruption unless the business line loses momentum.")
    elif f.action == "retirement":
        pts = {1: 14, 2: 6, 3: -15, 4: -20}[tier]
        mods.append((f"Planned retirement ({TIER_LABEL[tier]})", pts))
        parts.append(f"Planned retirement, not a surprise exit: {who} retires as {role}"
                     + (f" after about {f.tenure_years} years" if f.tenure_years else "") + (f" (effective {f.effective})" if f.effective else "") + ".")
        parts.append("Watch for who takes over the portfolio; the filing does not name a successor." if tier <= 3 else "")
    elif f.action == "appointment":
        pts = {1: 22, 2: 10, 3: -4, 4: -12}[tier]
        mods.append((f"New appointment ({TIER_LABEL[tier]})", pts))
        parts.append(f"New {role}: {who}" + (f", effective {f.effective}" if f.effective else "") + ".")
        if tier <= 2:
            parts.append("A new leader at this level can reset priorities — read the profile and track the next earnings call.")
    else:
        parts.append(f"Change involving {who} ({role}).")
    return mods, " ".join(p for p in parts if p)


def management_headline(f: MgmtFacts, shareholder_vote: bool = False) -> str | None:
    if not f.people:
        return None
    if shareholder_vote:
        return f"Shareholders approve {f.people[0]}" + (f" as {f.role}" if f.role else "")
    verb = {"reappointment": "reappointed", "appointment": "appointed", "resignation": "resigns", "retirement": "retires",
            "cessation": "ceases", "demise": "passes away"}.get(f.action or "", "— role change")
    head = f"{f.people[0]} {verb}" + (f" as {f.role}" if f.role else "")
    tail = ", ".join(x for x in (f.term, f"from {f.effective}" if f.effective and f.action in ("reappointment", "appointment") else None) if x)
    return head + (f" ({tail})" if tail else "")


# ------------------------------------------------------------------ event identity (deduplication)

STOP = set("the of and to a in for on by with as is are was be at from this that an its our has have been under regulation lodr "
           "sebi disclosure announcement intimation outcome update updates bank company limited ltd".split())


def content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP and len(t) > 2}


FUND_INSTRUMENTS = [
    ("warrants", r"\bwarrants?\b"), ("qip", r"qualified institutions?\s+placement|\bqip\b"),
    ("ncd", r"non[- ]convertible debentures?|\bncds?\b"), ("commercial_paper", r"commercial papers?"),
    ("rights", r"rights issue"), ("preferential", r"preferential (?:issue|allotment)"), ("bonds", r"\bbonds?\b"),
]
FUND_PRICE = re.compile(r"(?:\bat|price of)\s*(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I)
FUND_COUNT = re.compile(r"([\d.]+)\s*(crore|lakh)\s+(?:warrants|equity shares|shares|ncds|debentures)", re.I)


def fund_raise_key(text: str) -> str | None:
    """Identity of a capital-raise transaction: instrument + per-unit price (or unit count), so the board approval,
    the allotment and a newspaper copy of the same issue group into one event while a separate QIP stays separate."""
    t = text or ""
    inst = next((name for name, rx in FUND_INSTRUMENTS if re.search(rx, t, re.I)), None)
    if not inst:
        return None
    if m := FUND_PRICE.search(t):
        return f"fund:{inst}:{float(m.group(1).replace(',', '')):g}"
    if c := FUND_COUNT.search(t):
        return f"fund:{inst}:{c.group(1)}{c.group(2)[0].lower()}"
    return None


def event_key(category: str, facts: dict, text: str = "") -> str | None:
    """Stable identity for filings that describe the same underlying corporate event."""
    if category == "management_change" and facts.get("people"):
        fam = "continuity" if facts.get("action") == "reappointment" else ("exit" if facts.get("action") in
                                                                           ("resignation", "cessation", "retirement", "demise") else "change")
        return f"mgmt:{surname(facts['people'][0])}:{fam}"
    if category == "credit_rating" and facts.get("agency"):
        return f"rating:{facts['agency'].lower()}"
    if category == "fund_raise":
        from app.services.financial_events import identity_from_dict

        return identity_from_dict(facts.get("financial_event")) or fund_raise_key(text)
    return None


def similar(a: str, b: str) -> float:
    ta, tb = content_tokens(a), content_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def size_context(amount_cr: float | None, revenue_cr: float | None) -> str | None:
    if amount_cr is None or not revenue_cr:
        return None
    share = finance.ratio(amount_cr, revenue_cr).value
    if share is None:
        return None
    amt = f"₹{amount_cr:,.2f} cr" if amount_cr < 100 else f"₹{amount_cr:,.0f} cr"
    return f"{amt} ≈ {share * 100:.3f}% of annual revenue" if share < 0.001 else f"{amt} ≈ {share * 100:.1f}% of annual revenue"


# ------------------------------------------------------------------ card text + event DNA

def clean_subject(subject: str, company_name: str = "") -> str:
    """'Re-appointment of Mr. X as the Whole-time Director (designated as Executive Director) of HDFC Bank Limited (“Bank”)'
    -> 'Re-appointment of Mr. X as the Whole-time Director (designated as Executive Director)'."""
    t = re.sub(r"\s+", " ", subject or "").strip(" .")
    t = re.sub(r"\s*\((?:“|\")?(?:the )?(?:bank|company|corporation)(?:”|\")?\)\s*$", "", t, flags=re.I)
    base = re.escape(company_name.split(" Ltd")[0].split(" Limited")[0]) if company_name else ""
    if base:
        t = re.sub(rf"\s+(?:of|by)\s+(?:the\s+)?{base}[\w\s.&]*?(?:Limited|Ltd\.?)?\s*$", "", t, flags=re.I)
    t = re.sub(r"^(?:intimation|disclosure|announcement)\s+(?:under|of)\s+regulation\s+30[^-–:]*[-–:]\s*", "", t, flags=re.I)
    return t[:220]


def management_sentence(f: "MgmtFacts") -> str | None:
    if not f.people:
        return None
    who, role = f.people[0], f.role
    if role:
        verb = {"reappointment": f"re-appointed as {role}", "appointment": f"appointed as {role}", "resignation": f"resigned as {role}",
                "retirement": f"retired as {role}", "cessation": f"ceased to be {role}", "demise": f"passed away (was {role})"}.get(
            f.action or "", f"role change ({role})")
    else:   # no role could be read from the filing: say only what is known, never a placeholder like "the role"
        verb = {"reappointment": "re-appointed", "appointment": "appointed", "resignation": "resigned", "retirement": "retired",
                "cessation": "ceased to hold office", "demise": "passed away"}.get(f.action or "", "role change")
    s_ = f"{who} {verb}"
    if f.term:
        s_ += f" for {f.term}"
    if f.effective:
        s_ += f" from {f.effective}" if f.action in ("reappointment", "appointment") else f", effective {f.effective}"
    extras = []
    if f.regulator_approval:
        extras.append(f"{f.regulator_approval} has approved")
    if f.shareholder_approval_pending:
        extras.append("shareholder approval is pending")
    if f.shareholder_approval_obtained:
        extras.append(f"shareholders approved the appointment as {f.approved_role}" if f.approved_role and f.approved_role != f.role
                      else "shareholders have approved")
    if f.immediate:
        extras.append("with immediate effect")
    if f.reason:
        extras.append(f"reason given: {f.reason}")
    return s_ + ("; " + "; ".join(extras) if extras else "") + "."


ROLE_PATTERNS: list[tuple[str, str]] = [
    ("Repeat (newspaper copy)", r"newspaper|publication of"),
    ("Regulatory approval", r"approval (?:from|of) the (?:reserve bank|rbi|irdai|sebi|cci)|\brbi\b[^.]{0,60}approv|competition commission"),
    ("Shareholder approval", r"postal ballot[^.]{0,40}(?:outcome|result)|voting results?|shareholders? (?:have )?approved|outcome of (?:the )?(?:agm|egm|annual general)"),
    ("Revised terms", r"revis|amend|corrigendum|modification|change in (?:the )?(?:date|terms|record date)"),
    ("Completed", r"complet|consummat|allot(?:ted|ment) of|closure of (?:the )?(?:issue|offer)|stands? concluded"),
    ("Board decision", r"board[^.]{0,80}(?:approved|approves|considered and approved)|outcome of (?:the )?board|board meeting outcome|re-?appointment of|appointment of"),
    ("Advance notice", r"intimation|prior notice|notice of (?:board|meeting)|will be held|is scheduled"),
    ("Results filing", r"financial results|results for the quarter"),
    ("Press release / presentation", r"press release|media (?:statement|release)|presentation|shareholders? letter"),
]


def filing_role(text: str) -> str:
    for label, rx in ROLE_PATTERNS:
        if re.search(rx, text or "", re.I):
            return label
    return "Disclosure"
