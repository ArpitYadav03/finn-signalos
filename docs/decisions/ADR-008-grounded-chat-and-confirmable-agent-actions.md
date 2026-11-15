# ADR-008: Grounded research chat and an alerts agent whose actions need confirmation

**Status:** accepted · **Date:** 2026-11-15

**Context.** Users want to talk to FINN: follow-up questions while researching a company ("what caused this?",
"what's the latest?"), and plain-language control of alerts ("pause the Reliance alert and warn me if Maruti drops 5%").
The brief requires Pull analysis to be grounded in primary sources — no news media, broker notes or model memory.

**Decision.**
* **Research chat** = multi-turn version of the Research agent on the reasoning tier. Tools: recent exchange filings,
  read a filing, EOD price moves, results snapshot, multi-year financials, document search, promise tracker,
  calculators. "News" is answered from the company's own exchange filings (primary disclosures). Every answer goes
  through two deterministic checks: citations must refer to evidence retrieved in that turn (others are stripped), and
  every figure must appear in a tool output (others are listed as "verify before relying"). Prior turns are sent
  as plain text without their citation markers. Price moves are described as *coinciding* with filings, never
  *caused by* them unless the filing says so.
* **Alerts assistant** = agent on the fast tier. Read tools run immediately; write tools only produce typed,
  validated proposals (`services/alert_ops.py`). Validation errors are returned to the model so it can correct
  itself. The user applies proposals with one tap; nothing changes otherwise.
* Both degrade to deterministic planners when no key is configured or the provider fails, and the UI shows the mode.

**Consequences.** Conversational UX without giving up the evidence-first contract. Model cost is bounded per turn
(step cap 6–8, call cap per run, cheap tier for alerts). The deterministic number check can produce false positives
when the model rounds unusually; that errs on the side of caution.
