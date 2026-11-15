# ADR-009: Event identity and fact-based materiality for filings

**Status:** accepted · **Date:** 2026-11-15

**Context.** Real data showed (a) two HDFC Bank filings about one reappointment (RBI approval on 12 Nov, board approval
on 13 Nov) shown as separate signals; (b) keyword scoring firing on letter boilerplate ("in furtherance to our
intimation…" → "advance notice"; "CFO" in an appointee's past roles → "top executive"); (c) roles taken from the
signature block ("Company Secretary"); (d) generic "why it matters" text.

**Decision.**
* Parse Reg-30 letters into subject / body / annexure (`services/filing_facts.py`). Facts come from the subject and
  body only: people (honorifics, initials, apostrophes; substring-deduped), most senior role near the person,
  action (appointment, reappointment, resignation, retirement/superannuation, cessation), term, effective date,
  regulator approval, pending shareholder approval, "with immediate effect", stated reason, tenure.
* Event identity: `mgmt:<surname>:<continuity|exit|change>` (and `rating:<agency>`). A filing joins an earlier event of
  the same company if the key matches within 30 days, or if it is the same category within 3 days with near-identical
  subject text (Jaccard ≥ 0.5). The Briefing shows one card per event: the latest filing as the current state, the
  highest score among its filings (stated as a reason line), and every filing as a source.
* Leadership scoring is structural: continuity (reappointment) −20; unplanned exits scaled by role tier
  (MD/CEO/CFO +28, board executive +16, …); planned retirements below unplanned exits; new top-level appointments +22;
  shareholder ratification of a new MD & CEO +38 on a postal-ballot baseline. Keyword modifiers read only the title,
  subject line and listing subject — never the letter body.
* "Why it matters" is generated from those facts (continuity vs disruption, approvals pending, succession signal) and
  from size context for amounts (₹ normalised to crore and compared with annual revenue).
* Generic titles are classified from the filing's own subject line when the title is uninformative.

**Consequences.** Fewer, truer signals with explanations an analyst can audit. Heuristics are English/Reg-30 specific;
unusual letter formats fall back to the previous behaviour.
