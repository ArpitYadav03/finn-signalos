"""Filing Intelligence workflow: exchange filing -> prioritised, explained, cited signal card.

Pipeline (cheapest step first):
 1. rules classifier on the title             (free)
 2. LLM classification only if confidence low  (fast model, optional)
 3. fetch + extract the filing PDF only for categories where content changes the card
 4. deterministic fact extraction (names, ratings, ₹ amounts) + structured results numbers
 5. explainable score (services/scoring.py)
 6. optional LLM polish of headline/summary, whose quotes must exist verbatim in the filing text
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories import CATEGORY_LABELS, Category
from app.llm.client import get_llm, tracked_run
from app.llm.prompts import FILING_PROMPT_VERSION, FILING_SYSTEM
from app.models import Announcement, Company, DocumentChunk, EventAnalysis, UserPreference
from app.services import market as market_svc
from app.services import filing_facts as ff
from app.services import financial_events
from app.services.classifier import classify
from app.services.metrics import quarter_for_filing, results_snapshot
from app.services.scoring import score_event

log = logging.getLogger(__name__)
C = Category
CONTENT_CATEGORIES = {C.MANAGEMENT_CHANGE, C.CREDIT_RATING, C.MA, C.LITIGATION, C.FUND_RAISE, C.CAPEX,
                      C.PLEDGING, C.DIVIDEND, C.PRESS_RELEASE, C.BONUS_SPLIT, C.POSTAL_BALLOT, C.AGM_EGM}
SIZE_WHY = {
    C.LITIGATION: "Financial exposure is {ctx}{appeal}. Material only if it signals a wider compliance issue.",
    C.MA: "Deal size {ctx}. {scale}",
    C.FUND_RAISE: "Raise of {ctx}. {scale} Check the price, dilution and use of proceeds.",
    C.CAPEX: "Capex of {ctx}. {scale} Check funding (cash vs debt) and the payback timeline.",
    C.PRESS_RELEASE: "Disclosed value {ctx}. {scale}",
}

WHY = {
    C.RESULTS: "Results reset the earnings base the stock trades on; check whether profit kept pace with revenue.",
    C.MA: "Acquisitions and divestments change the business mix and capital allocation; check price paid and funding.",
    C.FUND_RAISE: "New equity can dilute existing holders and new debt adds leverage; check size, price and use of funds.",
    C.MANAGEMENT_CHANGE: "Leadership changes can shift strategy and execution; check who is coming in and why the change happened.",
    C.LITIGATION: "Legal or regulatory action can carry financial liability or business restrictions; check the amount and the company's stated response.",
    C.INSIDER_TRADING: "Insider buying or selling can signal how promoters view the stock; check size relative to their holding.",
    C.EARNINGS_CALL: "The call is where management explains numbers and gives guidance; FINN extracts any commitments into the Promise Tracker.",
    C.INVESTOR_MEETINGS: "Routine investor engagement; no new information is usually disclosed.",
    C.CREDIT_RATING: "Rating actions change the cost of debt and flag balance-sheet risk.",
    C.DIVIDEND: "Cash returned to shareholders; note the record date for eligibility.",
    C.BONUS_SPLIT: "Share count changes without any change in value; affects liquidity, not fundamentals.",
    C.CAPEX: "Capacity spend drives future growth but consumes cash; check size relative to the balance sheet.",
    C.PRESS_RELEASE: "General business update; material only if it discloses an order value or a strategic shift.",
    C.POSTAL_BALLOT: "Shareholder vote; relevant if the resolution is contested or changes capital structure.",
    C.AGM_EGM: "Shareholder meeting formality; read the resolutions only if something unusual is proposed.",
    C.PLEDGING: "Promoter pledges create forced-selling risk if the share price falls.",
    C.UNCLASSIFIED: "Routine compliance paperwork.",
}

DESIGNATION = re.compile(
    r"(chief executive officer|chief financial officer|chief operating officer|managing director|whole[- ]time director|"
    r"executive director|independent director|non[- ]executive director|company secretary|compliance officer|"
    r"chairman|chairperson|\bceo\b|\bcfo\b|\bcoo\b|\bmd\b|director|president|head of [a-z ]+)", re.I)
PERSON = re.compile(r"\b(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+([A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*){0,3})")
AMOUNT = re.compile(r"(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|million|billion|bn|mn)", re.I)
RATING = re.compile(r"((?:CRISIL|ICRA|CARE|IND|ACUITE|BWR)\s*\]?\s*[A-D]{1,3}[+-]?\s*(?:\(|/)?\s*(?:Stable|Positive|Negative|Watch[^)]*)?\)?)")
AGENCIES = re.compile(r"(CRISIL|ICRA|CARE Ratings|CARE|India Ratings|Fitch|Moody'?s|S&P|Acuit[eé]|Brickwork)", re.I)


def clean_title(title: str) -> str:
    t = re.sub(r"^Announcement under Regulation 30 \(LODR\)\s*-\s*", "", title, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -")
    return t[:1].upper() + t[1:] if t else title


GENERIC = re.compile(r"^(acquisition|allotment|meeting updates?|updates?|disclosure under regulation 30.*|"
                     r"general updates?|clarification.*|press release|change in directorate|"
                     r"announcement under regulation 30.*|outcome of board meeting|board meeting intimation.*|"
                     r"scheme of arrangement|credit rating|corporate insolvency resolution process.*)$", re.I)


def is_generic_title(title: str) -> bool:
    t = clean_title(title)
    return bool(GENERIC.match(t)) or len(t.split()) <= 3


class LLMFilingOutput(BaseModel):
    category: str
    headline: str = Field(max_length=140)
    summary: str = Field(max_length=500)
    why_it_matters: str = Field(max_length=300)
    materiality_adjustment: float = Field(ge=-10, le=10)
    adjustment_reason: str = Field(max_length=120)
    quotes: list[str] = Field(default_factory=list, max_length=4)


def _filing_text(db: Session, ann: Announcement) -> tuple[str, list[DocumentChunk]]:
    from app.ingestion.pipeline import index_filing_pdf  # local import avoids a cycle

    doc = index_filing_pdf(db, ann)
    if doc is None:
        return "", []
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.chunk_index)).all()
    return "\n".join(c.content for c in chunks), list(chunks)


def _excerpt_citation(ann: Announcement, chunks: list[DocumentChunk], needle: str | None) -> dict:
    page, chunk_id, excerpt = None, None, None
    if needle and chunks:
        for c in chunks:
            i = c.content.lower().find(needle.lower()[:40])
            if i >= 0:
                page, chunk_id = c.page_number, c.id
                excerpt = c.content[max(0, i - 120): i + 280].strip()
                break
    if excerpt is None and chunks:
        page, chunk_id, excerpt = chunks[0].page_number, chunks[0].id, chunks[0].content[:400]
    return {"kind": "filing", "document_title": clean_title(ann.title), "source_url": ann.source_url,
            "document_type": "exchange_filing", "publication_date": ann.published_at.isoformat() + "Z",
            "page_number": page, "chunk_id": chunk_id, "relevant_excerpt": excerpt,
            "document_id": chunks[0].document_id if chunks else None}


def _management_facts(text: str, listing: str) -> dict:
    blob = f"{listing}\n{text}"
    facts: dict = {}
    persons = [p.strip() for p in PERSON.findall(blob)]
    if persons:
        facts["people"] = list(dict.fromkeys(persons))[:3]
    d = DESIGNATION.search(blob)
    if d:
        facts["designation"] = d.group(1).strip().title().replace("Ceo", "CEO").replace("Cfo", "CFO").replace("Md", "MD")
    # Decide the action from the exchange listing first (short, specific), then from the filing body.
    for src in (listing, text):
        if re.search(r"re-?appoint", src, re.I):
            facts["action"] = "reappointment"
        elif re.search(r"resign|cessation|step(s|ping)? down|retire", src, re.I):
            facts["action"] = "departure"
        elif re.search(r"appoint", src, re.I):
            facts["action"] = "appointment"
        if "action" in facts:
            break
    m = re.search(r"with effect from\s+([^.,;]{4,40})", blob, re.I)
    if m:
        facts["effective"] = m.group(1).strip()
    # Reg-30 appointment letters must include a brief profile of the appointee: a primary-source bio.
    m = re.search(r"brief\s+profile[^:\n]*[:\n]\s*(.{80,900}?)(?:\n\s*\n|disclosure of relationship|$)", text, re.I | re.S)
    if m:
        facts["profile"] = re.sub(r"\s+", " ", m.group(1)).strip()[:600]
    return facts


def _rating_facts(text: str, listing: str) -> dict:
    blob = f"{listing}\n{text}"
    facts: dict = {}
    a = AGENCIES.search(blob)
    if a:
        facts["agency"] = a.group(1)
    r = RATING.findall(blob)
    if r:
        facts["ratings"] = list(dict.fromkeys(x.strip() for x in r))[:3]
    for word, action in (("downgrad", "downgrade"), ("upgrad", "upgrade"), ("reaffirm", "reaffirmed"),
                         ("assign", "assigned"), ("withdraw", "withdrawn")):
        if re.search(word, blob, re.I):
            facts["action"] = action
            break
    return facts


RUPEES_ONLY = re.compile(r"(?:Rs\.?|₹|INR)\s*([\d,]{4,}(?:\.\d+)?)(?!\s*(?:crores?|cr\b|lakhs?|million|billion|bn|mn))", re.I)


def amounts_in_crore(text: str) -> list[float]:
    """All ₹ amounts in a text, normalised to crore (handles lakh/crore/million and bare-rupee figures)."""
    from app.services.finance import to_crore

    out = []

    def per_unit(m) -> bool:  # "₹1,010 each" / "₹992 per share" are prices, not deal sizes
        return bool(re.match(r"\s*(/-)?\s*(each|per\b|a share)", text[m.end(): m.end() + 14], re.I))

    for m in AMOUNT.finditer(text):
        if per_unit(m):
            continue
        unit = m.group(2).lower().rstrip(".").rstrip("s")
        unit = {"cr": "crore", "mn": "million", "bn": "billion"}.get(unit, unit)
        try:
            out.append(to_crore(float(m.group(1).replace(",", "")), unit))
        except ValueError:
            pass
    for m in RUPEES_ONLY.finditer(text):
        if per_unit(m):
            continue
        try:
            out.append(to_crore(float(m.group(1).replace(",", "")), "inr"))
        except ValueError:
            pass
    return out


def _amounts(text: str) -> list[str]:
    return list(dict.fromkeys(f"₹{m.group(1)} {m.group(2).lower().rstrip('.')}" for m in AMOUNT.finditer(text)))[:4]


def _results_card(db: Session, company: Company, ann: Announcement) -> tuple[str | None, str | None, list[dict], dict | None]:
    expected = quarter_for_filing(ann.published_at.date())
    snap = results_snapshot(db, company, expected)
    if snap is None:
        return None, None, [], None
    lines = {l["metric"]: l for l in snap["lines"]}

    def pct(d):
        return None if d["value"] is None else d["value"]

    rev_y, pat_y = pct(lines["revenue"]["yoy"]), pct(lines["pat"]["yoy"])
    parts = []
    if rev_y is not None:
        parts.append(f"revenue {rev_y:+.1f}% YoY")
    if pat_y is not None:
        parts.append(f"PAT {pat_y:+.1f}% YoY")
    elif lines["pat"]["yoy"]["note"]:
        parts.append(f"PAT {lines['pat']['yoy']['note']}")
    headline = f"{snap['quarter']}: " + ", ".join(parts) if parts else None
    why = None
    if rev_y is not None and pat_y is not None:
        if pat_y < rev_y - 5:
            why = "Profit grew well below revenue — examine cost lines, margins and other income before trusting the top line."
        elif pat_y > rev_y + 10:
            why = "Profit outpaced revenue — check whether margin expansion is operational or driven by other income / one-offs."
        elif rev_y < 0:
            why = "Revenue declined year on year — check whether the shortfall is demand, pricing or one segment."
        else:
            why = "Profit broadly tracked revenue; the read-through is in segment mix and management's outlook on the call."
    mchg = snap["margin"]["change_yoy_pp"]["value"]
    if mchg is not None and abs(mchg) >= 1 and why:
        why += f" {snap['margin']['label'].rstrip('*')} {mchg:+.1f}pp YoY."
    return headline, why, snap["lines"], snap


def analyze_announcement(db: Session, ann: Announcement) -> EventAnalysis:
    company = ann.company
    cls = classify(ann.title, ann.exchange_summary)
    category, conf, method = cls.category, cls.confidence, cls.method
    llm = get_llm()
    text, chunks = "", []
    facts: dict = {}
    key_metrics: list[dict] = []
    headline: str | None = None
    why: str | None = None
    summary = ann.exchange_summary
    citations: list[dict] = []
    requires_review = False

    with tracked_run(db, "filing_intelligence") as tracker:
        # Generic titles ("Disclosure under Regulation 30…") say nothing: read the filing and classify from its own
        # "Sub:" line, deterministically (no LLM needed). Material categories are always read for facts.
        if category in CONTENT_CATEGORIES or conf < 0.7 or is_generic_title(ann.title):
            try:
                text, chunks = _filing_text(db, ann)
            except Exception as e:  # never let one bad PDF break the feed
                log.warning("Filing text unavailable for %s: %s", ann.id, e)
                facts["text_unavailable"] = str(e)[:200]
            subj = ff.split_letter(text).subject if text else ""
            if subj and (conf < 0.7 or is_generic_title(ann.title)):
                re_cls = classify(subj, ann.exchange_summary)
                if re_cls.category != C.UNCLASSIFIED and re_cls.confidence >= conf:
                    category, conf, method = re_cls.category, re_cls.confidence, "rules-v1 (filing subject line)"

        if category == C.RESULTS:
            h, w, km, snap = _results_card(db, company, ann)
            if snap:
                headline, why, key_metrics = h, w, km
                facts["results_quarter"] = snap["quarter"]
                lines = {l["metric"]: l for l in km}
                bits = []
                for k in ("revenue", "pat"):
                    l = lines.get(k)
                    if l and l["value"] is not None:
                        y = l["yoy"]["value"]
                        bits.append(f"{l['label'].rstrip('*')} ₹{l['value']:,.0f} cr" + (f" ({'+' if y >= 0 else '−'}{abs(y):.1f}% YoY)" if y is not None else ""))
                if bits:
                    facts["what_changed"] = f"{snap['quarter']} ({snap['basis']}): " + "; ".join(bits) + "."
                citations.append({"kind": "structured_results", "document_title": f"Filed results — {snap['quarter']} ({snap['basis']})",
                                  "source_url": snap["source_url"], "document_type": "standardised_results",
                                  "reporting_period": snap["quarter"], "relevant_excerpt": "Headline lines as standardised from the company's filed quarterly results.",
                                  "page_number": None, "chunk_id": None, "document_id": None})
                facts["results_notes"] = snap["notes"]
            else:
                facts["results_note"] = "Structured numbers for this quarter are not yet available; open the filing."
        letter = ff.split_letter(text)
        if letter.subject:
            facts["subject"] = letter.subject[:300]
            summary = ff.clean_subject(letter.subject, company.company_name)  # the filing's own "Sub:" line (primary)
        structured_mods: list[tuple[str, int]] = []
        annual_rev = _annual_revenue(db, company.id)
        if category in (C.MANAGEMENT_CHANGE, C.POSTAL_BALLOT, C.AGM_EGM):
            mf = ff.management_facts(text, ann.exchange_summary or "", ann.title)
            if category == C.MANAGEMENT_CHANGE or (mf.people and (mf.tier or 9) <= 2):
                facts.update(mf.as_dict())
                mods, why = ff.management_assessment(mf)
                if category != C.MANAGEMENT_CHANGE:  # e.g. shareholders approving a new MD & CEO
                    mods = [(f"Resolution concerns {mf.role} ({mf.people[0]})", 38 if (mf.tier or 3) == 1 else 16)]
                    why = (f"Shareholders approved {mf.people[0]} as {mf.role}" + " — the leadership transition is now formally ratified. "
                           + "Track the new leader's first stated priorities on the next earnings call.")
                structured_mods += mods
                headline = ff.management_headline(mf, shareholder_vote=category != C.MANAGEMENT_CHANGE)
                wc = ff.management_sentence(mf)
                if wc:
                    facts["what_changed"] = wc if category == C.MANAGEMENT_CHANGE else f"Shareholders approved: {wc}"
        elif category == C.CREDIT_RATING:
            facts.update(_rating_facts(text, f"{ann.title}. {ann.exchange_summary or ''}"))
            act = facts.get("action")
            rating = facts.get("ratings", [None])[0]
            if act:
                why = {"downgrade": "A downgrade raises borrowing costs and flags balance-sheet stress; check the agency's stated reasons.",
                       "upgrade": "An upgrade lowers funding costs and signals improving credit strength.",
                       "reaffirmed": "No change in credit standing" + (f" ({rating})" if rating else "") + "; informational.",
                       "assigned": "A first rating on a new instrument; check the instrument size relative to the balance sheet.",
                       "withdrawn": "Rating withdrawn — usually because the instrument was repaid; check the reason in the filing."}.get(act)
        if category in (C.MA, C.FUND_RAISE, C.CAPEX, C.PRESS_RELEASE, C.LITIGATION, C.DIVIDEND):
            amts = _amounts(f"{ann.exchange_summary or ''}\n{text}")
            if amts:
                facts["amounts"] = amts
            amts_cr0 = amounts_in_crore(f"{ann.exchange_summary or ''} {text[:6000]}")
            if amts_cr0:
                facts["largest_amount_cr"] = round(max(amts_cr0), 4)
            ctx = ff.size_context(max(amts_cr0) if amts_cr0 else None, annual_rev)
            scale = None
            if ctx and category in SIZE_WHY:
                share = max(amts_cr0) / annual_rev
                scale = ("Small relative to the company." if share < 0.002 else
                         "Large relative to the company." if share >= 0.05 else "Moderate relative to the company.")
                appeal = "; the company says it will appeal" if re.search(r"\bappeal", f"{text} {ann.exchange_summary or ''}", re.I) else ""
                why = SIZE_WHY[category].format(ctx=ctx, scale=scale, appeal=appeal)
                facts["size_context"] = ctx
            if category == C.FUND_RAISE:
                # Direction-aware: a redemption is money going out, not a raise (no dilution, no proceeds to deploy).
                # Read the filing itself; the index's one-line summary is third-party and only used when there is no text.
                fev = financial_events.parse(text or (ann.exchange_summary or ""), subject=facts.get("subject", ""), title=ann.title)
                if fev is not None:
                    if fev.amount is not None:   # the transaction's own amount, not the largest number in the letter
                        ctx = ff.size_context(fev.amount, annual_rev) or ctx
                        if ctx:
                            facts["size_context"] = ctx
                            share = fev.amount / annual_rev if annual_rev else None
                            scale = None if share is None else ("Small relative to the company." if share < 0.002 else
                                                                 "Large relative to the company." if share >= 0.05 else "Moderate relative to the company.")
                    facts["financial_event"] = fev.as_dict()
                    why = financial_events.why_it_matters(fev, ctx, scale)
                    structured_mods += financial_events.scoring_mods(fev)
                    from app.agents.claims import CRORE, fmt as fmt_value

                    amt = fmt_value(fev.amount, CRORE) if fev.amount is not None else None
                    inst = financial_events.INSTRUMENT_LABEL.get(fev.instrument or "", "securities")
                    floor = "floor price" in (fev.evidence.get("unit_price") or "").lower()
                    price = (f" (floor price ₹{fev.unit_price:,.2f} per share)" if floor else f" at ₹{fev.unit_price:,.2f} each") if fev.unit_price else ""
                    stage = financial_events.STAGE_LABEL.get(fev.stage, "Disclosure")
                    if fev.action in ("redemption", "repayment"):
                        facts["what_changed"] = (f"{inst[0].upper() + inst[1:]}" + (f" of {amt}" if amt else "") +
                                                 (" redeemed and paid to holders." if fev.stage == "completed" else " to be repaid."))
                    else:
                        facts["what_changed"] = f"{stage}: {inst}" + (f" worth {amt}" if amt else "") + price + "."

        # Optional LLM step (fast tier): only for low-confidence classification or material categories with text.
        llm_adj, llm_reason = None, None
        if llm and text and (conf < 0.7 or category in (C.MANAGEMENT_CHANGE, C.MA, C.LITIGATION, C.CREDIT_RATING, C.FUND_RAISE)):
            try:
                out = llm.structured(
                    tracker, "fast", FILING_SYSTEM,
                    f"Company: {company.company_name} ({company.ticker})\nFiling title: {ann.title}\n"
                    f"Rule-based category guess: {category.value}\nAllowed categories: {[c.value for c in Category]}\n"
                    f"<document>\n{text[:9000]}\n</document>",
                    LLMFilingOutput,
                )
                if out:
                    verified = [q for q in out.quotes if q and q.strip()[:60].lower() in text.lower()]
                    if out.quotes and not verified:
                        requires_review = True  # model quoted text that is not in the filing
                    else:
                        if conf < 0.7 and out.category in {c.value for c in Category}:
                            category, conf, method = Category(out.category), 0.75, f"llm:{llm.model_for('fast')}"
                        headline = headline or out.headline
                        summary = summary or out.summary
                        if not why and out.why_it_matters:
                            # Deterministic explanations take precedence. An AI-written one is kept only if every figure in
                            # it appears in the filing itself (e.g. "GNPA 1.06%" must be printed in the filing).
                            from app.agents import claims as _cl

                            src = f"{text or ''} {ann.exchange_summary or ''}"
                            toks = _cl.tokens(_cl.PERIOD.sub(" ", _cl.DATES.sub(" ", out.why_it_matters)))
                            if all(_cl._passage_supports(t, src) for t in toks):
                                why = out.why_it_matters
                            else:
                                facts["ai_why_rejected"] = "figures not found in the filing"
                        llm_adj, llm_reason = out.materiality_adjustment, out.adjustment_reason
                        for q in verified[:2]:
                            citations.append(_excerpt_citation(ann, chunks, q))
            except Exception as e:
                log.warning("LLM filing analysis skipped: %s", e)

        # Market context on filing day + novelty + preferences
        # Market context: only a session that traded entirely after publication counts (never the pre-filing bar).
        timing = market_svc.event_timing(db, company.id, ann.published_at)
        rx = timing["reaction"]
        mkt = {"return_pct": {"value": rx["return_pct"]}, "volume_ratio": {"value": rx["volume_ratio"]}} if rx else None
        facts["market_timing"] = {k: timing[k] for k in ("relation", "label", "pre_event")}
        from app.ingestion.pipeline import _event_group

        ann.event_group = _event_group(ann.company_id, ann.title, ann.published_at)   # re-analysis starts from the ingest grouping
        prior = find_same_event(db, ann, category, facts, summary)
        if prior is not None:
            ann.event_group = prior.event_group  # one event, several filings: keep both as sources
            facts["same_event_as"] = prior.id
        key = ff.event_key(category.value, facts, f"{ann.title} {facts.get('subject') or ''} {summary or ''} {ann.exchange_summary or ''}")
        if key:
            facts["event_key"] = key
        facts["filing_role"] = ff.filing_role(f"{ann.title} {facts.get('subject', '')} {ann.exchange_summary or ''}")
        if facts.get("financial_event"):   # the transaction's stage beats title wording ("Intimation of …" can be a completion)
            facts["filing_role"] = facts["financial_event"]["stage_label"]
        pref = db.scalars(select(UserPreference).where(UserPreference.key == "watched_categories")).first()
        watched = set(pref.value.get("categories", [])) if pref else set()
        thresholds = db.scalars(select(UserPreference).where(UserPreference.key == "thresholds")).first()
        th = thresholds.value if thresholds else {}
        amts_cr = amounts_in_crore(f"{ann.exchange_summary or ''} {text[:6000]}")
        # Keyword modifiers read the title, the filing's subject line and the listing subject — not the letter body,
        # whose boilerplate ("in furtherance to our intimation…", signatures) used to trigger false modifiers.
        sc = score_event(
            category, f"{ann.title} {letter.subject} {ann.exchange_summary or ''}",
            structured_mods=structured_mods,
            is_novel=prior is None,
            same_day_price_move_pct=(mkt or {}).get("return_pct", {}).get("value"),
            same_day_volume_ratio=(mkt or {}).get("volume_ratio", {}).get("value"),
            user_watched_categories=watched, llm_adjustment=llm_adj, llm_reason=llm_reason,
            max_amount_cr=max(amts_cr) if amts_cr else None, annual_revenue_cr=annual_rev,
            price_threshold_pct=th.get("price_move_pct", 4.0), volume_threshold=th.get("volume_multiple", 2.0),
        )

        if headline is None:
            if category == C.MANAGEMENT_CHANGE and facts.get("people"):
                verb = {"departure": "exits", "appointment": "appointed", "reappointment": "reappointed"}.get(facts.get("action", ""), "· role change")
                headline = f"{facts['people'][0]} {verb}" + (f" · {facts['designation']}" if facts.get("designation") else "")
            elif category == C.CREDIT_RATING and facts.get("agency"):
                headline = f"{facts['agency']} rating {facts.get('action', 'update')}" + (f": {facts['ratings'][0]}" if facts.get("ratings") else "")
            elif is_generic_title(ann.title) and ann.exchange_summary:
                headline = ann.exchange_summary  # the exchange listing's one-line subject is more informative
            else:
                headline = clean_title(ann.title)
        if why is None:
            why = WHY[category]
        if not any(c.get("kind") == "filing" for c in citations):
            citations.insert(0, _excerpt_citation(ann, chunks, facts.get("profile") or (summary or "")[:40]))
        tracker.tool_calls.append({"tool": "classify", "category": category.value, "confidence": conf})

    ea = ann.analysis or EventAnalysis(announcement_id=ann.id)
    ea.category = category.value
    ea.category_confidence = conf
    ea.headline = headline[:240]
    ea.summary = summary
    ea.why_it_matters = why
    ea.importance_score = sc.score
    ea.importance_level = sc.level
    ea.reasoning = sc.reasons
    ea.key_metrics = key_metrics
    ea.facts = facts
    ea.citations = citations
    ea.requires_review = requires_review or (conf < 0.6)
    ea.model_name = method
    ea.prompt_version = FILING_PROMPT_VERSION
    if ann.analysis is None:
        ann.analysis = ea  # link through the relationship so later filings in this session can see it (dedup)
        db.add(ea)
    ann.category = category.value
    ann.processing_status = "processed"
    db.commit()
    return ea


def _annual_revenue(db: Session, company_id: int) -> float | None:
    from app.services.metrics import load_series

    annual, _ = load_series(db, company_id, "annual")
    rev = annual.get("revenue", {})
    fy = sorted(p for p in rev if p.startswith("FY") and rev[p])
    return rev[fy[-1]] if fy else None


def find_same_event(db: Session, ann: Announcement, category: Category, facts: dict, summary: str | None) -> Announcement | None:
    """Earlier filing of this company describing the same underlying event, or None.

    1. Same event key (e.g. same person + same kind of leadership change) within 30 days.
    2. Same category, within 3 days, and near-identical subject text (Jaccard ≥ 0.5).
    3. Results/earnings-call filings on the same IST day (already grouped at ingest).
    """
    window_start = ann.published_at - timedelta(days=30)
    earlier = db.scalars(select(Announcement).where(
        Announcement.company_id == ann.company_id, Announcement.id != ann.id,
        Announcement.published_at <= ann.published_at, Announcement.published_at >= window_start,
        Announcement.processing_status == "processed").order_by(Announcement.published_at)).all()
    my_text = f"{facts.get('subject') or ''} {summary or ''} {ann.title}"
    key = ff.event_key(category.value, facts, f"{my_text} {ann.exchange_summary or ''}")
    for e in earlier:
        ea = e.analysis or db.scalars(select(EventAnalysis).where(EventAnalysis.announcement_id == e.id)).first()
        if ea is None:
            continue
        other = f"{ea.facts.get('subject') or ''} {ea.summary or ''} {e.title}"
        if key and (ea.facts.get("event_key") or ff.event_key(ea.category, ea.facts, f"{other} {e.exchange_summary or ''}")) == key:
            return e
        if e.event_group and e.event_group == ann.event_group:
            return e
        close = ann.published_at - e.published_at <= timedelta(days=3)
        other_key = ea.facts.get("event_key") or ff.event_key(ea.category, ea.facts, f"{other} {e.exchange_summary or ''}")
        if key and other_key and key != other_key:
            continue   # two identified transactions (e.g. different CP ISINs) are different events, however similar the subject
        if close and ea.category == category.value and category not in (C.UNCLASSIFIED, C.INVESTOR_MEETINGS):
            if ff.similar(my_text, other) >= 0.5:
                return e
    return None


def category_label(value: str) -> str:
    try:
        return CATEGORY_LABELS[Category(value)]
    except ValueError:
        return value
