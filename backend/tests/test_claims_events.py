"""Regressions for financial correctness: capital-event direction/stage, claim-level references, semantic number
validation and causal claims. Letter texts are SYNTHETIC paraphrases of real filing structures (no real amounts are
asserted about real companies); companies created here are flagged is_sample where the model supports it."""
from datetime import date

from app.agents import claims as cl
from app.services import filing_facts as ff
from app.services import financial_events as fe

CP_LETTER = ("Sub: Redemption of Commercial Paper. In terms of the SEBI Operational Circular dated 10th August 2021, regarding "
             "issue and listing of Non-convertible Securities and Commercial Paper, we wish to inform you that the Company has "
             "fully redeemed the Commercial Papers (CPs) of ₹ {amt} crore bearing ISIN - {isin} issued on 14th August 2026. "
             "The maturity proceeds of the CPs have been duly paid to all the holders today.")
QIP_APPROVAL = ("Outcome of Board Meeting. The Board of Directors at its meeting held today has approved raising of funds up to "
                "₹5,000 crore by way of a qualified institutions placement, subject to approval of the shareholders.")
WARRANT_APPROVAL = ("The Committee at its meeting held today, i.e. 10 November 2026, has approved the issue of 5,74,25,740 Warrants "
                    "to the Promoter, at an issue price of ₹ 1,010 (Rupees One Thousand and Ten only) per Warrant, for an amount "
                    "aggregating to ₹ 57,99,99,97,400.")
WARRANT_ALLOTMENT = ("The Allotment Committee has at its meeting held today approved the allotment of 5,74,25,740 Warrants to the "
                     "Promoter at the Issue price of Rs. 1,010 per Warrant convertible into equivalent number of Equity Shares of "
                     "face value of Re. 1 each.")


# ---------------------------------------------------------------- capital events

def test_cp_redemption_is_a_completed_repayment_not_a_raise():
    e = fe.parse(CP_LETTER.format(amt="1000", isin="INE280A14617"), subject="Redemption of Commercial Paper",
                 title="Intimation of Repayment of Commercial Paper (CP)")
    assert (e.instrument, e.action, e.capital_direction, e.stage) == ("commercial_paper", "redemption", "outflow", "completed")
    assert e.amount == 1000 and e.isins == ["INE280A14617"] and e.unit == "crore" and e.currency == "INR"
    assert "fully redeemed" in e.evidence["stage"]                       # the field is backed by the filing's words
    why = fe.why_it_matters(e, "₹1,000 cr ≈ 1.1% of annual revenue", "Moderate relative to the company.")
    assert "Raise" not in why and "use of proceeds" not in why and "Check the price" not in why
    assert "no new capital" in why and "no dilution" in why
    assert ("Routine debt repayment (money out, no new capital)", -35) in fe.scoring_mods(e)
    assert e.as_dict()["stage_label"] == "Completed"                       # not "Advance notice" despite "Intimation"


def test_different_cp_redemptions_are_different_events():
    a = fe.parse(CP_LETTER.format(amt="850", isin="INE280A14658"), subject="Redemption of Commercial Paper")
    b = fe.parse(CP_LETTER.format(amt="1000", isin="INE280A14617"), subject="Redemption of Commercial Paper")
    assert fe.event_identity(a) != fe.event_identity(b)
    assert ff.event_key("fund_raise", {"financial_event": a.as_dict()}) != ff.event_key("fund_raise", {"financial_event": b.as_dict()})


def test_approval_is_not_completion():
    e = fe.parse(QIP_APPROVAL, subject="Outcome of Board Meeting - Fund Raising")
    assert (e.instrument, e.action, e.capital_direction, e.stage) == ("qip", "issuance", "inflow", "board_approval")
    assert e.stage != "completed" and "nothing is raised yet" in fe.why_it_matters(e, None, None)
    approval, allotment = fe.parse(WARRANT_APPROVAL), fe.parse(WARRANT_ALLOTMENT)
    assert approval.stage == "board_approval" and allotment.stage == "allotment"   # allotted warrants are not "completed"
    assert approval.amount == 5799.9997 and approval.unit_price == 1010 and allotment.unit_price == 1010   # not the ₹1 face value
    assert fe.event_identity(approval) == fe.event_identity(allotment)          # same issue → one event, two stages


def test_buyback_is_money_out():
    e = fe.parse("The Board approved a buyback of equity shares for ₹2,000 crore at ₹4,000 per share.")
    assert e.action == "buyback" and e.capital_direction == "outflow" and "Buyback" in e.subtype


# ---------------------------------------------------------------- claim registry (annual cash conversion)

def _registry():
    reg = cl.ClaimRegistry("TESTCO")
    src = {"kind": "standardised_results", "title": "test", "url": None, "primary": None}
    ids = {
        "cfo26": reg.fact("cfo", "CFO", 7088, cl.CRORE, "FY26", "annual", "consolidated", src),
        "pat26": reg.fact("pat", "PAT", 4395, cl.CRORE, "FY26", "annual", "consolidated", src),
        "rev_q": reg.fact("revenue", "Revenue", 9977, cl.CRORE, "Q2FY27", "quarter", "consolidated", src),
        "pat_q": reg.fact("pat", "PAT", 1085, cl.CRORE, "Q2FY27", "quarter", "consolidated", src),
        "pat_ly": reg.fact("pat", "PAT", 1018, cl.CRORE, "Q2FY26", "quarter", "consolidated", src),
    }
    ids["conv26"] = reg.calc("cash_conversion", "Cash conversion", 1.61, cl.TIMES, "FY26", "annual", "consolidated", "ratio",
                             "CFO ÷ PAT", [ids["cfo26"], ids["pat26"]], "7,088 ÷ 4,395 = 1.61×")
    ids["pat_yoy"] = reg.calc("pat", "PAT YoY growth", 6.6, cl.PCT, "Q2FY27", "quarter", "consolidated", "yoy",
                              "(cur − base) ÷ |base| × 100", [ids["pat_q"], ids["pat_ly"]], "…", base_period="Q2FY26")
    ids["margin_pp"] = reg.calc("opm_pct", "EBITDA margin change", -2.0, cl.PP, "Q2FY27", "quarter", "consolidated", "pp_change",
                                "this − year ago", [ids["rev_q"]], "16.0% − 18.0% = −2.0 pp", base_period="Q2FY26")
    return reg, ids


def test_cash_conversion_keeps_annual_inputs():
    reg, ids = _registry()
    out = reg.as_dict({ids["conv26"]})
    conv = out[ids["conv26"]]
    assert conv["formula"] == "CFO ÷ PAT" and conv["period"] == "FY26" and conv["unit"] == cl.TIMES
    inputs = [out[i] for i in conv["inputs"]]
    assert {(i["metric"], i["period"], i["period_type"]) for i in inputs} == {("cfo", "FY26", "annual"), ("pat", "FY26", "annual")}
    assert ids["rev_q"] not in out                                       # the quarterly revenue is NOT part of this citation


def _verify(text, reg, passages=None):
    passages = passages or {}
    return cl.verify(text, reg, lambda e: passages.get(e, ""), other_entities=["Tata Steel"])


def test_validator_accepts_correct_claims():
    reg, ids = _registry()
    text = (f"FY26 cash conversion was 1.61× [{ids['conv26']}]: CFO ₹7,088 crore [{ids['cfo26']}] against PAT ₹4,395 crore "
            f"[{ids['pat26']}]. Q2FY27 PAT rose 6.6% YoY [{ids['pat_yoy']}].")
    out, rep = _verify(text, reg)
    assert not rep.removed and out.count("[") == 4


def test_validator_rejects_sign_reversal():
    reg, ids = _registry()
    out, rep = _verify(f"Q2FY27 PAT fell 6.6% YoY [{ids['pat_yoy']}].", reg)
    assert rep.removed and "sign" in rep.removed[0]["reason"] and "6.6" not in out
    _, rep2 = _verify(f"EBITDA margin improved by 2.0 pp YoY in Q2FY27 [{ids['margin_pp']}].", reg)
    assert rep2.removed and "sign" in rep2.removed[0]["reason"]


def test_validator_rejects_unit_swaps():
    reg, ids = _registry()
    for wrong in ("₹9,977 lakh", "9,977%", "9,977×"):
        _, rep = _verify(f"Q2FY27 revenue was {wrong} [{ids['rev_q']}].", reg)
        assert rep.removed, wrong
    _, rep = _verify(f"Q2FY27 PAT growth was 6.6 pp YoY [{ids['pat_yoy']}].", reg)   # % stated as pp
    assert rep.removed


def test_validator_rejects_wrong_period_basis_entity_and_metric():
    reg, ids = _registry()
    cases = {
        "period": f"FY25 cash conversion was 1.61× [{ids['conv26']}].",
        "basis": f"Standalone FY26 CFO was ₹7,088 crore [{ids['cfo26']}].",
        "entity": f"Tata Steel's FY26 CFO was ₹7,088 crore [{ids['cfo26']}].",
        "metric": f"FY26 revenue was ₹4,395 crore [{ids['pat26']}].",       # the number exists, but it is PAT
    }
    for name, text in cases.items():
        _, rep = _verify(text, reg)
        assert rep.removed, name


def test_matching_number_elsewhere_is_not_enough():
    reg, ids = _registry()
    # No reference, and the clause names revenue: 4,395 exists in the registry only as PAT → rejected.
    out, rep = _verify("FY26 revenue reached ₹4,395 crore.", reg)
    assert rep.removed and out == ""
    # Unreferenced but semantically matching figures are linked to their exact claim.
    out, rep = _verify("FY26 PAT was ₹4,395 crore.", reg)
    assert not rep.removed and f"[{ids['pat26']}]" in out and rep.resolved == 1


def test_document_figures_must_be_in_the_cited_passage():
    reg, _ = _registry()
    passages = {"E1": "Total contract value for the quarter was US$9.6 billion, steady sequentially."}
    out, rep = _verify("Deal TCV was US$9.6 billion [E1].", reg, passages)
    assert not rep.removed
    _, rep = _verify("Deal TCV was US$12.4 billion [E1].", reg, passages)
    assert rep.removed


def test_unsupported_answer_becomes_insufficient():
    reg, _ = _registry()
    out, rep = _verify("Management targets ₹9,999 crore of orders.", reg)
    assert rep.insufficient and out == ""


# ---------------------------------------------------------------- causal claims

def test_cause_without_passage_is_removed_but_observation_stays():
    reg, ids = _registry()
    out, rep = _verify(f"Q2FY27 EBITDA margin fell 2.0 pp YoY [{ids['margin_pp']}], reflecting higher input costs.", reg)
    assert "reflecting" not in out and "2.0 pp" in out and rep.explanations_removed and not rep.removed


def test_cause_with_supporting_passage_is_kept():
    reg, ids = _registry()
    passages = {"E2": "Margins were pressured by raw material costs during the quarter."}
    out, rep = _verify(f"Q2FY27 EBITDA margin fell 2.0 pp YoY [{ids['margin_pp']}], reflecting raw material costs [E2].", reg, passages)
    assert "reflecting raw material costs" in out and rep.explanations_supported and not rep.explanations_removed
    # A passage that is cited but says nothing about the stated cause does not establish it.
    passages = {"E3": "The Board approved the annual accounts and recommended a dividend."}
    out, rep = _verify(f"Q2FY27 EBITDA margin fell 2.0 pp YoY [{ids['margin_pp']}] due to higher freight costs [E3].", reg, passages)
    assert "freight" not in out and rep.explanations_removed


# ---------------------------------------------------------------- guidance: history and analysts are not promises

def test_historical_performance_is_not_guidance():
    from app.services.guidance import extract_guidance

    ref = date(2026, 4, 20)
    hist = ("Our operating margin for the year stood at 21%. We delivered revenue growth of 4.2% in FY26, "
            "and margins were maintained at 21% through the year.")
    assert extract_guidance(hist, ref) == []
    fwd = "For FY27 we expect operating margin of 20% to 22%."
    got = extract_guidance(fwd, ref)
    assert got and got[0].target_min == 20 and got[0].target_max == 22 and got[0].target_period == "FY27"


def test_analyst_statements_are_not_management_guidance(db):
    from app.ingestion.pipeline import extract_document_guidance
    from app.models import Company, Document, DocumentChunk, ManagementGuidance

    co = Company(ticker="GUIDCO", company_name="Guidance Test Co (synthetic)")
    db.add(co)
    db.flush()
    doc = Document(company_id=co.id, document_type="concall_transcript", title="Synthetic call", status="indexed",
                   source_url="https://example.test/guidco.pdf", publication_date=date(2026, 10, 20), page_count=2)
    db.add(doc)
    db.flush()
    db.add_all([
        DocumentChunk(document_id=doc.id, chunk_index=0, page_number=1, speaker="An Analyst", speaker_role="analyst",
                      content="We expect you to deliver 30% EBITDA margin in FY28; our model assumes revenue growth of 25% next year."),
        DocumentChunk(document_id=doc.id, chunk_index=1, page_number=2, speaker="The CFO", speaker_role="management",
                      content="We expect EBITDA margin of 18% to 19% for FY27."),
    ])
    db.commit()
    extract_document_guidance(db, doc)
    rows = db.query(ManagementGuidance).filter_by(company_id=co.id).all()
    assert rows and all(r.speaker == "The CFO" for r in rows)
    assert not any(r.target_value == 30 or r.target_min == 30 or r.target_value == 25 for r in rows)


def test_grouped_and_mismatched_references_are_resolved_semantically():
    reg, ids = _registry()
    # Model cites the inputs as a comma group; the 2.0 pp change is resolved to the change calculation itself.
    out, rep = _verify(f"Q2FY27 EBITDA margin fell by 2.0 pp YoY [{ids['cfo26']}, {ids['pat26']}].", reg)
    assert not rep.removed and f"[{ids['margin_pp']}]" in out
    # A wrong reference does not rescue a wrong number.
    _, rep = _verify(f"Q2FY27 EBITDA margin fell by 3.0 pp YoY [{ids['margin_pp']}].", reg)
    assert rep.removed


def test_relative_periods_resolve():
    reg, ids = _registry()
    out, rep = _verify("Q2FY27 PAT was ₹1,085 crore, up from ₹1,018 crore a year ago.", reg)
    assert not rep.removed and f"[{ids['pat_ly']}]" in out
    _, rep = _verify("Q2FY27 PAT was ₹1,018 crore.", reg)            # the year-ago value claimed for the current quarter
    assert rep.removed


def test_bullet_label_names_the_metric():
    """Regression (held-out eval): '- **Revenue:** Reported at ₹9,977 crore' was removed because the label was stripped."""
    reg, ids = _registry()
    out, rep = _verify("- **Revenue:** Reported at ₹9,977 crore for Q2FY27.", reg)
    assert not rep.removed and f"[{ids['rev_q']}]" in out
    _, rep = _verify("- **PAT:** Reported at ₹9,977 crore for Q2FY27.", reg)      # label says PAT, value is revenue
    assert rep.removed


def test_real_guidance_extraction_failures_are_fixed():
    """Regressions from live transcripts (Nov 2026): history mentioning a guided range, an H1 actual, R&D as % of sales,
    and past-progressive commentary are not guidance; a genuine statement still is."""
    from app.services.guidance import extract_guidance

    ref = date(2026, 10, 31)
    not_guidance = [
        "For H1, operating margin stood at 21% at the midpoint of our guided range.",
        "And this year, first half is 11%.",
        "We expect our FY27 R&D spend to be 6% to 7% of the sales for the next year.",
        "Since the first half, we have been clocking at around double-digit top line growth versus our FY27 guidance of high single digit.",
        "Now, we will discuss the Nine Months Performance: For the first nine months, revenue grew 11.3%.",
        "We are now on a nine-month basis at 21% margin, which is mid of the range.",
        "nine-month basis at 21% margin, which is midpoint of the guidance that we have given.",
        "We ended the year with 21.1%, with an endeavor to increase margin going forward.",
        "We ended the year with 21.1%, with an endeavor to increase margins.",
    ]
    for s in not_guidance:
        assert [g for g in extract_guidance(s, ref) if g.guidance_type == "quantitative"] == [], s
    got = extract_guidance("Our operating margin guidance remains at 20% to 22% for the year.", ref)
    assert got and (got[0].target_min, got[0].target_max) == (20, 22)


def test_alert_signature_detects_equivalent_rules():
    from app.services import alerts as A

    a = A.AlertRuleSpec.model_validate({"ticker": "TCS", "logical_operator": "OR", "conditions": [
        {"metric": "daily_return_pct", "operator": "lt", "value": -4}, {"metric": "volume_ratio", "operator": "gt", "value": 2}]})
    b = A.AlertRuleSpec.model_validate({"ticker": "TCS", "logical_operator": "OR", "conditions": [
        {"metric": "volume_ratio", "operator": "gt", "value": 2.0}, {"metric": "daily_return_pct", "operator": "lt", "value": -4.0}]})
    c = A.AlertRuleSpec.model_validate({"ticker": "TCS", "logical_operator": "AND", "conditions": [
        {"metric": "volume_ratio", "operator": "gt", "value": 2}, {"metric": "daily_return_pct", "operator": "lt", "value": -4}]})
    d = A.AlertRuleSpec.model_validate({"ticker": "TCS", "conditions": [{"metric": "daily_return_pct", "operator": "gt", "value": 4}]})
    assert A.signature(a) == A.signature(b)              # order and int/float don't matter
    assert A.signature(a) != A.signature(c)              # AND vs OR are different rules
    assert A.signature(d) != A.signature(A.AlertRuleSpec.model_validate({"ticker": "TCS", "conditions": [
        {"metric": "daily_return_pct", "operator": "lt", "value": -4}]}))   # direction matters


def test_event_links_only_checkable_guidance(db):
    from types import SimpleNamespace as NS

    from app.models import Company, Document
    from app.services.promise import event_links

    co = Company(ticker="LINKCO", company_name="LinkCo (synthetic)")
    db.add(co)
    db.flush()
    doc = Document(company_id=co.id, document_type="concall_transcript", title="call", status="indexed", source_url="https://x.test/call.pdf")
    db.add(doc)
    db.commit()

    def item(i, status="long_term", basis="reported", caveats=(), actual=20.0, doc_id=doc.id):
        return {"id": i, "guidance_type": "quantitative", "metric_label": "EBITDA margin", "target_text": "20–24%", "target_period": None,
                "horizon": "long_term", "basis": basis, "statement": "s", "speaker": "CFO", "revision": {"previous_target": "20–24%", "changed": False},
                "guidance_citation": {"document_id": doc_id, "page_number": 4}, "actual_citation": None, "explanations": [], "section": "aspiration",
                "comparison": {"status": status, "actual_value": actual, "actual_period": "Q2FY27", "method": "m", "caveats": list(caveats)}}
    tracker = {"items": [item(1), item(2, basis="segment"), item(3, caveats=["margin type inferred from the company's usage"]),
                         item(4, status="not_comparable"), item(5, actual=None), item(6, doc_id=999)]}
    ann = NS(analysis=NS(category="earnings_call", facts={}), company=co, source_url="https://x.test/call.pdf")
    links = event_links(db, ann, tracker=tracker)
    assert [l["id"] for l in links] == [1] and links[0]["relation"] == "reiterated"
    assert event_links(db, NS(analysis=NS(category="dividend", facts={}), company=co, source_url="x"), tracker=tracker) == []


def test_ncd_allotment_with_redeemable_wording_is_an_issuance():
    """Real PERSISTENT filing (4 Nov 2026): 'redeemable' describes the instrument; the event is an allotment (money in)."""
    from app.services import financial_events as fe

    text = ("Outcome of the Special Committee Meeting held on November 4, 2026 in relation to the allotment of Non-Convertible "
            "Debentures. The Committee approved the allotment of 700,000 (Seven Lakh) INR denominated, senior, rated, listed and "
            "redeemable non-convertible debentures (“NCDs”) having a face value of INR 0.10 million each aggregating to INR 70 billion.")
    ev = fe.parse(text, subject="Outcome of the Special Committee Meeting in relation to the allotment of Non-Convertible Debentures",
                  title="Announcement under Regulation 30 (LODR)-Allotment")
    assert ev.action == "issuance" and ev.capital_direction == "inflow" and ev.stage == "allotment"
    # a genuine redemption still reads as one
    red = fe.parse("The Company has redeemed the commercial paper on its maturity date and paid the holders in full.",
                   subject="Redemption of Commercial Paper", title="Announcement under Regulation 30 (LODR)")
    assert red.action == "redemption"


def test_cipla_reg30_table_name_and_c_suite_role():
    """Real CIPLA filing (4 Nov 2026): the Reg-30 table puts 'Reason for change' right after the name; the role is a
    C-suite title outside the fixed list. Expect the clean name, the verbatim title and no placeholder text."""
    from app.services import filing_facts as ff

    text = ("Pursuant to Regulation 30 of the SEBI Listing Regulations, we hereby inform that the Board of Directors have approved "
            "the appointment of Mr Vikram Janakiraman, as the Global Chief Technical Operating Officer, designated as a Senior "
            "Management Personnel of the Company, with effect from 7th December, 2026. The details required under the SEBI Master "
            "Circular dated 30th January, 2026 Name Mr Vikram Janakiraman Reason for change viz. appointment, resignation, removal, "
            "death or otherwise Appointment of Mr Vikram Janakiraman as the Global Chief Technical Operating Officer.")
    f = ff.management_facts(text, listing="", title="Announcement under Regulation 30 (LODR)-Change in Management")
    assert f.people[0] == "Vikram Janakiraman"
    assert f.role == "Global Chief Technical Operating Officer" and f.action == "appointment"
    assert f.effective == "7th December, 2026" and not f.shareholder_approval_pending
    s = ff.management_sentence(f)
    assert "Reason" not in s and "the role" not in s
    assert s.startswith("Vikram Janakiraman appointed as Global Chief Technical Operating Officer from 7th December, 2026")


def test_unknown_role_never_renders_a_placeholder():
    from app.services import filing_facts as ff

    f = ff.MgmtFacts(people=["A. B. Kumar"], action="appointment")
    assert ff.management_sentence(f).startswith("A. B. Kumar appointed") and "the role" not in ff.management_sentence(f)


def test_revenue_share_is_not_growth_guidance():
    """Real Infosys sentences (Oct 2026 call): a share of revenue is history, not a revenue-growth promise."""
    from datetime import date

    from app.services.guidance import extract_guidance

    for sent in ["Against this backdrop, the key outcomes for the quarter were - Revenue growth was below our expectations, "
                 "while AI first revenue now accounts for 10.5% of our revenues.",
                 "Q2 AI revenue is now 10.5% of total revenue with strong sequential growth of over 29%.",
                 "AI revenue was a standout at 10.5% of our revenue, growing at 29% sequentially.",
                 "This deal is expected to contribute over 1% revenue growth in the coming financial year.",
                 "This deal is expected to contribute over 1% growth for the company in FY28.",
                 "So we feel quite good now that the next year, that is why we were sharing that from the mega deal, we estimate "
                 "something like a 1% revenue growth outlook from that one deal that we have signed in Q2."]:
        assert extract_guidance(sent, date(2026, 10, 31)) == []
    cut = extract_guidance("We are revising our full year revenue growth guidance to 1% to 2%, reflecting softer-than-expected demand.",
                           date(2026, 10, 31))
    assert [(g.metric, g.target_min, g.target_max) for g in cut] == [("revenue_growth", 1.0, 2.0)]


ASIANPAINT_LETTER = (
    "Sir/Madam, Sub: Disclosure under Regulation 30 of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, "
    "2015 - Shareholders’ approval for the appointment of Mr. Leo Puri as Independent Director of the Company The Company, vide "
    "its letter no. APL/SEC/57/2026-27/35 dated 7th September 2026, had informed the stock exchanges that the Board of Directors "
    "of the Company, at its meeting held on the same day, had unanimously approved the appointment of Mr. Leo Puri (DIN: 01764813) "
    "as an Additional and Independent Director of the Company for a first term of five (5) consecutive years commencing from 7th "
    "September 2026 to 6th September 2031, subject to the approval of the shareholders of the Company through Postal Ballot. Mr. "
    "Leo Puri shall assume office as Chairman of the Board with effect from 23rd January 2027, upon completion of the term of Mr. R. "
    "Seshasayee (DIN: 00047985) as Chairman and Independent Director on 22nd January 2027. This is to inform you that, based on the "
    "voting results of the Postal Ballot conducted through remote e-Voting, the shareholders of the Company have approved the "
    "appointment of Mr. Leo Puri as an Independent Director of the Company.")


def test_asianpaint_approval_obtained_and_future_chair_appointment():
    """Real ASIANPAINT letter (6 Nov 2026). Previously: 'Leo Puri ceases as Chairperson; shareholder approval is pending'."""
    from app.services import filing_facts as ff

    f = ff.management_facts(ASIANPAINT_LETTER, listing="Shareholders approved Leo Puri as independent director; he becomes chairman from 23 January 2027.",
                            title="Announcement under Regulation 30 (LODR)-Change in Directorate")
    assert f.people[0] == "Leo Puri" and f.action == "appointment"          # Seshasayee's term ending is not Puri's departure
    assert f.shareholder_approval_obtained and not f.shareholder_approval_pending   # the "subject to" clause is quoted history
    assert f.approved_role == "Independent Director" and f.effective == "23rd January 2027"
    s = ff.management_sentence(f)
    assert "cease" not in s and "pending" not in s
    assert s == "Leo Puri appointed as Chairperson from 23rd January 2027; shareholders approved the appointment as Independent Director."


def test_a_genuine_departure_and_a_genuine_pending_approval_still_read_correctly():
    from app.services import filing_facts as ff

    gone = ff.management_facts("Sub: Cessation of Mr. A. K. Rao as Director. Mr. A. K. Rao ceased to be a Director of the Company upon "
                               "completion of his term on 31st October 2026.", listing="", title="Change in Directorate")
    assert gone.action == "cessation"
    pending = ff.management_facts("The Board approved the appointment of Mr. B. C. Das as Independent Director, subject to the approval of "
                                  "the shareholders of the Company.", listing="", title="Change in Directorate")
    assert pending.shareholder_approval_pending and not pending.shareholder_approval_obtained
