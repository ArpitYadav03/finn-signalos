# Investor workflow — covering note and three-minute demo

Date: 15 Nov 2026. One workflow: **identify** a meaningful change → **verify** it → **investigate** it → **monitor** what
happens next. Every example below is real data in the local database; screenshots are in `docs/screenshots/w*.jpg`.

## Mandatory requirements vs differentiators

The original ENAM brief (from the session's first message; no separate document exists in the repository):

| Brief item | Priority in the brief | Status |
|---|---|---|
| Push: windowed digest (24 h default, configurable window), classify, prioritise, collapse routine, summaries, source links | Required | ✅ (the window digest stays below the review feed) |
| Push: triggered alerts — categories, EOD price/volume, configurable thresholds | Required | ✅ |
| Pull: any NSE ticker, primary documents only, citations | Required | ✅ (unfamiliar tickers resolved live) |
| 15–20 holding portfolio; 3–5 demo research companies | Required | ✅ 18 holdings |
| Signal Radar, Evidence Lens | Critical | ✅ |
| Management Promise Tracker | High | ✅ (extraction fixes below) |
| Natural-language alert builder; five-minute brief | Medium | ✅ / partial (Briefing is the brief) |
| Narrative drift; bull vs bear | Optional | ⬜ / extractive only |
| **Since your last review**, explicit review state, next-evidence watch, Event DNA ↔ Promise Ledger, claim-level verification, durable ingestion | Not in the brief — differentiators | ✅ this round and earlier rounds |

## What changed this round

1. **Since your last review** (`GET /api/v1/review`, `ReviewFeed`): *Updated since your review* (a reviewed event got a
   new filing — the new filings are shown first, with their role), *New* (material, never reviewed), *Unresolved —
   watching* (waiting on a milestone you chose). A first visit says what the feed is and offers "Start tracking from
   now"; it marks nothing. Only **Mark reviewed** or **Watch for this** records a review (`event_reviews` stores which
   filings existed at review time, so a later filing brings the event back). Viewing the page never changes state
   (tested).
2. **Lean cards:** What happened · What is new (or *New since your review*) · Why it deserves attention · View evidence ·
   Investigate (+ Mark reviewed). Figures, Event DNA timeline, guidance connection, next evidence, transaction details
   and scoring open on demand.
3. **Event DNA ↔ Promise Ledger** (`promise.event_links`): an earnings-call event links to guidance extracted from *that*
   transcript; a results event links to guidance whose comparison uses its quarter. Shown only when the comparison is
   checkable: comparable status, company-level basis, the measure named in the statement (not inferred), an actual value.
4. **Next evidence to watch** (`services/milestones.py`): derived from extracted facts or rules — shareholder approval
   when a filing says "subject to approval of the shareholders"; allotment after a board approval or issue opening;
   warrant conversion within 18 months of allotment (SEBI ICDR Reg. 162, date computed); next quarterly results due
   within 45/60 days of quarter end (SEBI LODR Reg. 33). **Watch** is labelled "suggested monitoring, not an alert";
   "Turn into an alert" pre-fills the alert builder, where the rule is reviewed before saving.
5. **Concise research** (`research-v8`): conclusion → *Relevant figures* table (built from cited claims, with period,
   value, basis, source) → Evidence bullets → Open questions. Each line is labelled **Fact**, **Reported figure**,
   **Calculation**, **Interpretation** or **Open question**.
6. **Alerts:** equivalent proposals collapse (order-free canonical signature; direction and AND/OR are part of
   equivalence); a proposal identical to an existing rule is flagged and unticked. Roles, direction, thresholds and
   AND/OR were already preserved; outcomes show matched / not matched / unable to evaluate.
7. **Correctness fixes found while building the demo:** the guidance extractor treated "first half is 11%", "stood at
   21% … our guided range", "we ended the year with 21.1%" and "R&D spend 6–7% of sales" as guidance, and kept a
   chunk-truncated "guidance remains at 20%" instead of the full "20% to 22% for the year". All fixed with regressions
   from the real sentences; 91 transcripts re-extracted (407 → 390 rows). Card "Why it deserves attention" written by the
   model is now kept only if every figure in it appears in the filing.

## Three-minute demonstration (real examples)

| Time | Step | What to show | Evidence |
|---|---|---|---|
| 0:00 | **Identify** | Briefing → "Since your last review": *N new*. Bajaj Finance card: "Allotment: warrants at ₹1,010.00 each." Why: equity raise, dilution; 2 filings | `w01`, `w06` |
| 0:30 | **Meaningful change** | Expand: Event DNA *Board / committee approval* (₹5,800 crore) → *Allotment*, both 10 Nov; Titan's two CP redemptions sit in routine as *Debt repayment … completed* (not raises) | `w02` |
| 0:50 | **Exact primary evidence** | View evidence → stage quote "approved the allotment of 5,74,25,740 Warrants … Issue price of Rs. 1,010 per Warrant" on the BSE filing, page 1 | evidence sheet |
| 1:10 | **Monitor** | Next evidence: *Conversion of warrants into shares* due by 11 May 2028 (ICDR Reg. 162) → Watch for this → moves to *Unresolved — watching*, labelled "not an alert" | `w02` |
| 1:30 | **Investigate** | Research ITC: "How did Q2 FY27 revenue and profit compare with a year ago?" → conclusion, figures table (revenue ₹20,269 crore vs ₹19,502 crore, +3.9%; PAT ₹4,229 crore vs ₹5,187 crore, −18.5%, consolidated), open question | `w04` |
| 2:00 | **Verified guidance comparison** | Infosys Promise Ledger: FY26 operating-margin guidance 20–22% (Jan 2026 call p.3, first stated Apr 2025, repeated Jul/Oct 2025) → FY26 EBIT margin (42,280 − 4,902) ÷ 178,650 = 20.92%, consolidated, delivered; CFO's own "full year margins is at 21%" (Apr 2026 call p.7) matches | `w05` |
| 2:20 | **Event ↔ guidance** | Research Pidilite (outside the portfolio): earnings call of 5 Nov → Guidance connection: EBITDA-margin corridor 20–24% reiterated (unchanged since May 2026) vs Q2 FY27 OPM 20.0% (context, not scored) | `w03` |
| 2:40 | **Accurate alert** | Alerts: "Alert me if Reliance falls more than 4% in a day or its volume exceeds 2x the average, and on any CFO change" → preview: *falls more than 4%* OR *volume exceeds 2×* — flagged **same as your existing alert**; *leadership change involving the CFO* kept as a separate rule | API-verified |
| 2:50 | **Coverage limit** | Briefing 30-day window: "Partial coverage — the filing source lists only the latest filings per company"; complete from 9 Nov | earlier `reliability` doc |

## Verification

* Backend **137 tests pass** (new: review workflow — viewing doesn't mark, watch → unresolved → later filing → *Milestone
  reached* → reviewed → reopen; event-link rules; alert signatures; real-sentence guidance regressions). Frontend `tsc`,
  `next lint`, `next build` clean. Migration `9c4d2e7f1b20` up/down.
* Browser, 390 px: review feed and lean cards (no horizontal scroll); expanded Event DNA, next evidence, transaction;
  Watch → Unresolved with the "not an alert" note; Pidilite guidance connection; ITC concise answer with labels and
  figures table; Infosys ledger; evidence sheet scrolls internally while the page stays put, focus moves to Close,
  Escape closes and returns focus to "View evidence" at the same scroll position. Desktop 1440 px review feed.
* Hand checks: Infosys FY26 EBIT margin 20.92% from stored FY26 figures; ITC YoY growths; warrant stage quote.
* Earlier rounds (still valid): loading skeletons, missing data ("no end-of-day price loaded"), recoverable failures
  (question kept with Retry), bottom-nav hiding while typing.
* After testing I removed my test review action; the review state keeps only the "started" time that was set in the
  browser during this session.

## Trade-offs and omissions

* **One real event-guidance link.** The strict rule leaves exactly one qualifying link today (Pidilite). Infosys has the
  best verified comparison, but its earnings call is older than the latest-five filings the source lists, so there is
  no Infosys event to attach it to; it is demonstrated in the Promise Ledger instead. Titan's "11% margin target" was
  excluded because it refers to the jewellery segment while the ledger labelled it company-level.
* **Pidilite's corridor** applies to the second half of FY27 per the quote; the extractor files it as undated, so it is
  shown as context, not scored.
* **Review state is single-workspace** (everyone shares one review history) and has no undo history beyond "reopen".
* **Milestones** cover capital raises, leadership approvals and results due dates; project completion dates and
  shareholder-meeting dates are not yet parsed from filings.
* **Model-written card explanations** are figure-checked against the filing, but their wording is not otherwise verified.
* Not deployed; no screen-reader pass; narrative drift not built.
