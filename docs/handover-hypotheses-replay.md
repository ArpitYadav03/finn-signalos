# Investor Handover · Competing Hypotheses · Historical Alert Replay

Date: 15 Nov 2026. These three connected capabilities let an investor (1) hand FINN a monitoring plan and come back to
*changes since the last review*, (2) test competing explanations against filed evidence, and (3) test a plan on past
data before relying on it. Examples below are real data in the local database unless marked **synthetic** (tests only,
flagged `is_sample`) or **simulated** (acceptance step; reverted afterwards).

Every result states **what happened**, **why it relates to the question or plan**, **the evidence**, **what remains
uncertain** and **the next action**, and says when data is end-of-day, cached or unavailable.

## M0 — correctness first (before building anything new)

| Problem in the brief | Status | How it is enforced |
|---|---|---|
| Commercial-paper redemption shown as fundraising | Fixed earlier, re-verified (Titan: "Debt repayment — commercial paper redeemed") | `financial_events.parse` direction model |
| **New this round:** NCD/CP/QIP *allotments* shown as "to be repaid" because the NCDs are described as "redeemable" | **Fixed** — PERSISTENT ₹7,000 cr NCDs, ₹600 cr CP and ₹4,265 cr QIP now read as money raised; only Titan's two real CP repayments remain redemptions | redemption must be an *act* ("has redeemed", "redemption of"), not an attribute; regression test with the real filing text |
| Annual cash-flow citations opening quarterly figures | Fixed earlier (primary annual-report page) | `metrics.primary_annual_reference` |
| Validator accepting wrong sign/unit/period | Fixed earlier | `claims.verify` |
| Causal claims cited with generic numbers | Fixed earlier | causal check requires a document passage |
| Equivalent alert proposals indistinguishable | Fixed earlier | canonical `signature` |
| **New:** live plan evaluation ignored **AND** between price and volume | **Fixed** — one shared `market_check` (AND needs every leg; a false leg settles it) used by live monitoring and replay | `test_market_check_and_or_semantics` |
| **New:** volume ratio computed from as few as 16 prior sessions while labelled "previous 20" | **Fixed** — needs 20 prior sessions of history; up to 20 % missing values inside that window still tolerated | `finance.volume_ratio`, replay tests |
| **New:** "Since your last review" reopened a reviewed event when only a *repeat* was filed | **Fixed** — same rule as Handover | `test_review_feed_also_ignores_repeats` |
| **New:** first review action created the review-state row twice (500 error) | **Fixed** | `_set_review_state` flush |

**Evidence contracts** (`backend/app/domain/evidence.py`): `FinancialFact` (company, metric, value with sign, unit,
period, basis, source), `CalculatedResult` (formula + expression + input facts), `DocumentClaim` (document id, version =
content hash, page, chunk, verbatim passage — built only if the quote is found in the chunk), and the item kinds *fact /
calculation / document / interpretation / insufficient*. Handover guidance evidence and Competing Hypotheses use them.

## M1 — Investor Handover

**Workflow:** Alerts → *Monitoring plans* → **What should FINN watch?** → *Review plan* → *Save plan* → FINN evaluates after
each refresh (08:30 and 18:30 IST) → Briefing → **Changes since your last review** → *Investigate* or *Mark reviewed*.

* **Plan** (`services/monitoring.py`, typed `PlanSpec`): scope (named tickers or all holdings), conditions —
  `filing` (category + roles + *departure / appointment* + rating action), `guidance_cut` (same measure, target period and
  basis; lower midpoint), `fundraise_update` (announced or moves stage), `price_move` (direction, %), `volume` (× previous
  20 sessions) — and AND/OR (AND only between price and volume on the same session). Unsupported asks (intraday, technical
  indicators, news/broker views, absolute price levels) are listed as *Not included* before saving. One clarification only
  when a plan would otherwise be wrong ("How large a one-day move should count?").
* "Watch my holdings for guidance cuts, CFO resignations and changes to announced fundraising" →
  *All 18 holdings*: (1) management lowers a numeric guidance target, (2) a fund raise is announced or moves to a new stage,
  (3) **a departure (resignation, cessation or retirement) of the CFO** — a CFO *appointment* does not match (tested).
* **Persistence:** `monitoring_plans`, `monitoring_plan_versions` (edits create a new version), `plan_evaluations`
  (unique *plan, version, fingerprint* → refreshes and concurrent runs add nothing). Pause/resume; duplicate save returns
  the existing plan.
* **Briefing sections:** *Unable to evaluate* (source failure ≠ no change), *Meaningful updates* (new non-repeat filing in a
  reviewed event — the new filing first), *New*, *Resolved questions* (milestone reached by a later filing), *Still open*
  (milestones with due dates), *Repeated disclosures not shown as new*, plus saved investigations whose evidence changed.
  Each item: why it relates to the plan, what changed, timestamps, *View evidence*.
* **Review semantics:** opening the briefing marks nothing. *Mark reviewed* records the exact filing ids shown (the item's
  version); a filing that arrives after the page loaded stays unreviewed (tested). Concurrent first reviews merge.

**Acceptance (real data, browser at 390 px):** one saved plan (double-click saved once) · *repeated disclosure*:
PERSISTENT NCD allotment reviewed, its repeat filing listed under "not shown as new" · *meaningful update*: Bajaj Finance
warrants reviewed at the approval (10 Nov 12:12 IST), the allotment (12:23 IST) shows as *Not in the version you reviewed*
and the "Allotment" question as *resolved* · *source failure* (**simulated** HTTP 503 on Titan's filings check): "Filings
after 2026-11-15 07:10 UTC are unknown — this is not the same as no change" · *return visit* and **persistence after a
backend restart**: plan, versions and reviews intact. Also real: Infosys FY27 revenue-growth guidance cut **1.5–3.5 % →
1–2 %** (constant currency, 30 Apr 2026 → 31 Oct 2026 calls) with both verbatim passages linked to their pages. The two
simulated reviews and the simulated failure were removed afterwards; the demo plans remain.

## M2 — Competing Hypotheses

**Workflow:** Research → company → **For & against** → question → *Suggest hypotheses* (2, editable, labelled *Suggested
by FINN · standard template*; add a third) → *Compare evidence* → *Open figures & inputs* / *Open passage* → *Save
investigation* → FINN re-checks after each refresh → *Evidence changed* in the investigation and in the Handover briefing.

* **Scope (deliberately narrow):** questions about weaker **cash conversion / working capital**, answered from annual
  statements. Other questions get "not a question this tool covers yet".
* **Structured evidence** (`services/hypotheses.py`, arithmetic in `finance.py`): cash conversion = CFO ÷ operating profit;
  latest year vs median of earlier years (premise check, ≥ 5 pp weaker); duration (two weak years vs one vs recovered);
  working-capital-days change; a three-year rising build; debtor / inventory / payable days (context). Each is a
  calculation with formula, expression and input facts.
* **Document evidence:** BM25 retrieval with queries for *and against* each hypothesis; overlapping chunks de-duplicated;
  one bounded model call (≤ 8 passages) classifies each passage per hypothesis with a verbatim quote — a judgement whose
  quote is not in the passage, or that rests on a numeric table row, is discarded. Without a model, passages show as
  unclassified context. Quarterly passages are flagged as not comparable with annual figures.
* **Summary states (no probabilities, no verdict):** *conflicting*, *one-sided* ("a description of the evidence, not a
  conclusion"), *insufficient*, *premise not supported*; refusals: *incompatible* (quarterly/half-year cash conversion;
  lenders), *unsupported question*. Missing evidence and next questions are listed; absence of discussion is a gap, not
  disproof.
* **Persistence:** `investigations`, `investigation_versions` (new version only when the evidence fingerprint changes),
  `reviewed_version` (only explicit "Mark changes seen"; never moves backwards), cached AI judgements reused on revisit.
  Scheduled re-checks make no model calls.

**Acceptance (real data):**

| Case | Company | Result |
|---|---|---|
| Conflicting evidence | **LT** (saved, browser) | FY26 conversion 46.1 % vs 78.8 % median FY22–24 (−32.8 pp). Two weak years (26 %, 46 %) support *persistent*; working-capital days +6 and the Jul 2026 call ("expected utilization of customer advances and settlement of vendor dues") support *temporary*. Limitation shown: consolidated CFO includes Financial Services |
| Insufficient evidence | **SUNPHARMA** | Working-capital days fell 65 → 54 (challenges *temporary*); only FY26 is weak, so persistence cannot be judged; missing: cash flow after FY26, management's explanation. One model judgement on a cash-flow table row was discarded |
| Incompatible periods | **TITAN** "Q2 FY27 cash conversion" | Refused: FINN holds full-year cash flows only; quarterly profit ÷ annual cash flow is not computed |
| Incompatible company | HDFC Bank | Refused: a lender's operating cash flow is loans and deposits |
| Premise not supported | TCS | FY26 72.0 % vs 70.8 % median — nothing weaker to explain |

Every figure opens its inputs (LT: 9,151 ÷ 34,551 = 26.5 %, 16,741 ÷ 36,345 = 46.1 %, checked by hand); every passage
opens its page.

## M3 — Historical Alert Replay

**Workflow:** Alerts → plan → **Test this plan on past data** → dates → *Check coverage* → *Run replay* (progress, Cancel)
→ matched sessions, duplicates suppressed, unable-to-evaluate, limitations → *Resume* if interrupted.

* **Same rules as live:** `monitoring.market_check`, `monitoring.filing_matches`, `monitoring.guidance_cuts(as_of=…)`; the
  run stores the exact plan version and spec. Separate from live monitoring: no alerts, no plan changes, no return or
  accuracy claims.
* **Point-in-time:** prices up to the session (volume = previous 20 sessions; first session has no previous close);
  filings published by the end of the session day (IST) — filings on non-trading days roll into the next session; event
  roles rebuilt from filings known by then (a later allotment or repeat cannot leak back); guidance dated on or before the
  session. Mode *FINN had indexed it* uses the ingestion time instead of publication time; *public then, indexed later*
  is labelled.
* **Session calendar** = every date with any stored bar, so a company's missing bar is *missing data*, not a holiday.
  Filing conditions are *unable to evaluate* before a company's continuous-coverage date.
* **Durable:** `replay_runs` saves results after every session; cancel between sessions; a restart marks running replays
  *interrupted*; *Resume* continues from the last saved session without duplicates; a double submit returns the running replay.

**Acceptance:** synthetic tests cover a known match (−6 %, 4.17× volume), a non-match, a missing bar, insufficient volume
history ("10 of 20 prior sessions"), a revision (guidance cut and allotment) published after the replay end, duplicate
suppression, indexed-vs-public timing, cancel/interrupt/resume. **Real data:** "falls more than 4 % or volume above 2.5×"
over 8 Sep – 13 Nov 2026 (45 sessions, 22 non-trading days): 16 matched sessions (e.g. INFY −4.3 % on 9 Sep, ITC −4.0 %
with 3.38× volume on 8 Oct); 378 unable checks = 18 companies × 20 sessions without 20 prior volumes + 18 first-session
price checks. The guidance/fund-raise plan replayed to 9 Nov shows Bajaj's 5 Nov disclosure but **not** the 10 Nov
allotment, and the Infosys cut appears only from the 2 Nov session (call on Saturday 31 Oct).

## Three-minute demo (ENAM)

| Time | Step | Show |
|---|---|---|
| 0:00 | Hand over | Alerts → Monitoring plans → type the guidance-cut / CFO-resignation / fund-raise sentence → *Review plan*: three conditions, departure-only CFO, schedule 08:30/18:30 IST → *Save plan* (`h03` shows the saved plan list) |
| 0:35 | Return briefing | Briefing → *Changes since your last review*: Infosys guidance 1.5–3.5 % → 1–2 % with both passages (open p.4); Bajaj approval → allotment; PERSISTENT repeat not shown as new; *Still open*: warrant conversion due 11 May 2028 (`h02`) |
| 1:15 | Source failure | Explain *Unable to evaluate* (screenshot `h02` shows the simulated 503): "not the same as no change" |
| 1:30 | Competing hypotheses | Research LT → For & against → suggested H1/H2 → *Compare evidence* → conflicting: two weak years vs working-capital build and management's own words; open the figures (inputs) and a passage; note the Financial Services limitation; *Save investigation* (`h04`) |
| 2:15 | Replay | Alerts → price/volume plan → *Test this plan on past data* → coverage ("volume needs 20 prior sessions: earliest 8 Oct") → run → 16 matched sessions, 378 unable checks explained, no return claims |
| 2:45 | Point-in-time | Replay the handover plan to 9 Nov: Bajaj allotment absent; Infosys cut only from 2 Nov |

## Verification

* Backend **152 tests pass** (new: `test_handover.py` 6, `test_hypotheses.py` 4, `test_replay.py` 4, finance and
  financial-event regressions). Migrations `a1d5f3c8e2b0`, `b2e6a4d9f3c1`, `c3f7b5e0a4d2` upgrade and downgrade.
  Frontend `tsc`, `next lint`, `next build` clean.
* Browser 390 px: plan composer (draft preserved across reload; focus moves to the review), save (double-click → one plan),
  return briefing (no horizontal scroll; evidence sheet opens p.4 of the Infosys call; Escape returns focus to the link),
  For & against (LT compare → save; figures sheet with inputs), replay (coverage → run → results). Desktop 1440 px:
  Alerts plans, Research For & against, Briefing — no horizontal scroll, heading order h1 → h2.
* Screenshots: `docs/screenshots/h02` (return briefing, 390 px, plus a full-length version), `h03` (plans, desktop), `h04` (LT For & against, 390 px). No replay screenshot: you were using the browser pane during that step, so
  I verified the replay through the page DOM in a background tab rather than taking over your view.

## Limitations (honest)

* **Competing Hypotheses covers one question family** (cash conversion / working capital, annual data). Other questions
  are refused rather than answered loosely. Hypothesis wording can be edited, but the computed rules test the *temporary*
  and *persistent* stances; a third, custom hypothesis is tested against passages only.
* Working-capital, debtor, inventory and payable days are the standardised-data source's year-end figures, not FINN's own
  calculation. Half-year cash-flow statements are not extracted, so "has it recovered?" waits for the next annual figure.
* Model classifications of passages are labelled and quote-checked, not verified as conclusions; wording of explanations
  can still be imperfect (e.g. a passage only about order flow marked "doesn't distinguish" instead of "context").
* **Replay** uses FINN's *current* analysis of each filing and only the latest version of each document; only ~45 sessions
  of prices are stored (8 Sep – 13 Nov 2026), so volume rules are evaluable from 8 Oct; filing coverage is complete only
  from late Oct / early Nov for most holdings. Replays run in a thread of the API process (resumable after restart), not
  a separate worker; one process is assumed.
* Review state is single-workspace (shared by everyone using the deployment). Handover plans evaluate on the daily refresh
  schedule; there are no push notifications.
* Guidance-cut detection is strict (same named measure, explicit target period and basis); undated or "unspecified"
  guidance is ignored, so some real cuts (e.g. Infosys's July 2026 statement without a year) are not compared.
