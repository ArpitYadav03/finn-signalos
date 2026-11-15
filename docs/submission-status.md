# Final submission status — FINN SignalOS

Date: 15 Nov 2026 · Branch `fix/frontend-review-issues` (uncommitted) · Backend frozen for this pass.

## Status: READY WITH DISCLOSED LIMITATIONS (application) · submission links still missing

Update (15 Nov 2026, latest): **A, B and the ASIANPAINT card are fixed in the backend** with regression tests from the real
filings and re-analysis (see "Backend fixes" below). Two limitations remain, both disclosed in the UI and neither changes a
displayed conclusion:
* **L&T (P2):** one AI-classified quarterly passage covering a segment-excluded figure still counts as evidence. The investigation's
  "conflicting" verdict is the same with computed evidence alone (checked).
* **P3:** in the non-default "FINN had indexed it" replay mode, guidance cuts use the statement date, not the indexing date. The panel says so.

The deployment, repository and build-history links are still missing, so the *submission package* is not complete.

## Correctness issues

| # | Issue | Status | Evidence (reproduced 15 Nov 2026) |
|---|---|---|---|
| A | CIPLA management change | **Fixed (backend)**, see below. Original evidence: | Announcement 122: headline "Vikram Janakiraman Reason appointed (from 7th December, 2026)", `people: ["Vikram Janakiraman Reason"]`, no `role`, what_changed "appointed as the role". The filing names him Global Chief Technical Operating Officer, effective 7 Dec 2026 (a future appointment) |
| B | INFY false promise | **Fixed (backend)**, see below. Original evidence: | Guidance 3024: "AI first revenue now accounts for 10.5% of our revenues" stored as `revenue_growth` target 10.5% (constant currency), shown in the Promise Ledger under *aspiration* beside FY26 reported revenue growth 9.6%, with "not scored" as the only caveat |
| C | Freshness / coverage | **Fixed (frontend)** | Briefing now shows four distinct times from existing fields: closing-price session (13 Nov), last complete refresh of all holdings (`collection.finished_at`, 09:31 IST), filings covered through (`coverage.filings_source.checked_through`, 12:39 IST), and latest single update (`freshness.announcements_checked_at`, 21:52 IST). The old "Filings checked 7h ago" label used the latest single ingestion |
| D | Financial provenance | **Fixed where frontend-only** | Removed "Every claim links to its page". Standardised tables are labelled secondary. The figures sheet now lists inputs with no matched primary page (e.g. "No primary page matched for F2, F5"). Calculations keep period, basis, formula and inputs. Remaining dependency: inputs without primary pages depend on backend matching |
| — | ASIANPAINT Leo Puri card | **Fixed (backend)** | Now "Leo Puri appointed as Chairperson (from 23rd January 2027)", shareholders approved the Independent Director appointment, nothing pending, event key `mgmt:puri:change` |
| — | L&T quarterly / ex-Financial-Services passage counted as evidence | Still present (backend) | Presentation now separates it from the annual consolidated figures; stored relation unchanged (P2) |
| — | Indexed-mode replay guidance cutoff | Still present (backend) | Disclosed in the replay UI (P3) |

### Repair specifications (A and B; P1–P3 are in `review-fixes-and-backend-spec.md`)

**A. Management-change person/role extraction**
* **Reproduce:** `GET /api/v1/signals?hours=720` → CIPLA card 122; or run `filing_facts.management_facts` on the filing text.
* **Expected:** person "Vikram Janakiraman"; role "Global Chief Technical Operating Officer" (Senior Management Personnel); action appointment; status: board approved; effective 2026-12-07 (future, not yet in office). Headline: "Cipla appoints Vikram Janakiraman as Global Chief Technical Operating Officer from 7 Dec 2026".
* **Code path:** `backend/app/services/filing_facts.py` `people()` (it captured "Reason" from the Reg-30 disclosure table header "Name Mr Vikram Janakiraman Reason for change…"), `role_near()` / `DESIGNATION` (designations not in the CEO/CFO list return None), and the what_changed template in `agents/filing_agent.py` (it renders "the role" when the role is None).
* **Regression test:** a fixture with the real CIPLA text asserting name, role, future effective date, and that no headline contains "the role" or a word after the surname. Then re-analyse management changes and recompute their `event_key`s.
* **Submission impact:** high. A wrong name and a placeholder role appear on a demo-visible Briefing card.

**B. Revenue share misread as growth guidance**
* **Reproduce:** `GET /api/v1/companies/INFY/promise-tracker` → item id 3024.
* **Expected:** not guidance at all: it is a historical share of revenue ("accounts for 10.5% of revenues"). It should be excluded, or at most kept as a non-forward statement, never compared with company revenue growth.
* **Code path:** `backend/app/services/guidance.py`: `HISTORICAL` / `STATES_GUIDANCE` classification and metric mapping (share-of-revenue phrasing such as "accounts for X% of revenue" should be `NOT_GUIDANCE`). Also `services/promise.py`, so that a statement without a forward cue never reaches *aspiration*.
* **Regression test:** the real sentence must produce no guidance row. The two sibling sentences ("AI revenue was … 10.5% of our revenue", "AI revenue is now 10.5% of total revenue") must not either. Then re-extract INFY transcripts.
* **Submission impact:** high. It creates a false management promise in the Promise Ledger, a headline feature.

## Frontend changes in this pass

* Briefing: compact greeting; the large shortcut tiles that duplicated navigation were removed; "Today" renamed **Digest** (default: previous 24 hours, selected chip highlighted). Tiles show actual counts ("4 active plans", not events). The quiet window gives one explanation and one primary action ("Show the last 7 days"). The search box now says "Search an NSE symbol" (symbols only).
* Freshness and coverage labels (C) and provenance copy (D), as above.
* Digest and Unreviewed cards now pass `from`, so Research shows "← Back to briefing" to the same view.
* For & against: the supported scope (cash conversion / working capital, full-year statements) is stated before question entry.

## Verification (actual)

* Frontend: 13 tests pass (`npm test`); `tsc`, `next lint` and `next build` are clean. Backend regression: **152 tests pass** (unchanged code).
* Browser journeys (real data, 390 px unless noted), all passed:
  1. Digest (24h, quiet) → Show the last 7 days → 3 developments → View evidence (overlay scrolls, Escape returns focus to the trigger).
  2. Investigate → Research with the filing in context and the question pre-set → Back to briefing returns to the same view.
  3. Portfolio → Polycab research.
  4. PIDILITIND (outside the demo choices) opens; APPLE shows the honest "couldn't find" limitation.
  5. Plan preview keeps ITC, both thresholds and AND; nothing saved.
  6. Existing plan → coverage "Checked: 2026-10-14 to 2026-11-13 · rule v1" → replay finished, 10 matched sessions, exact rule shown.
  7. Saved L&T investigation → passage opens p.2 of the Jul 2026 call → figures sheet shows the formula and inputs.
  8. A forced research failure keeps the typed question and offers Retry.
* No horizontal overflow at 390, 768 and 1440 px on Briefing, Research, For & against, Alerts and Portfolio. Reduced-motion CSS is present. The bottom nav uses safe-area padding and hides while typing.
* Screenshots: `docs/screenshots/final-mobile-390.jpg`, `final-desktop-1440.jpg`.
* Not done: a screen-reader pass, and a physical-device keyboard test (only emulated).

## Missing submission items

* **Deployment URL: missing.** Not deployed; http://localhost:3000 is local only.
* **Repository URL: missing.** Not pushed; the GitHub upload was stopped by instruction.
* **Build-history URL: missing.** `docs/ai-build-history.md` exists locally only.
* The submission email has not been sent (by instruction).

## Three-minute demonstration (real data)

| Time | Step | Show |
|---|---|---|
| 0:00 | Relevant development | Briefing → Unreviewed → Bajaj Finance H1 FY27 results (PAT ₹12,447 cr, +28%), labelled "Older item · filed 8 days ago" |
| 0:30 | Exact evidence | View evidence → exchange filing page; Escape returns to the card |
| 0:50 | Contextual research | Investigate → Research with the filing in context; answer with the figures table (period, basis, source; secondary sources marked) → Back to briefing |
| 1:30 | Valid guidance comparison | Research INFY → Promise Ledger: FY26 operating-margin guidance 20–22% vs FY26 EBIT margin 20.92% = (42,280 − 4,902) ÷ 178,650, consolidated. **Avoid item 3024 (blocker B)** |
| 2:00 | Monitoring preview | Alerts → Monitoring plans → "Tell me if ITC falls more than 5% and its volume is above 2x on the same day" → preview: ITC, >5% **and** >2×, same session → do not save |
| 2:20 | Historical replay | Existing price/volume plan → Test this plan on past data → coverage → run → matched sessions and the "no return claims" note |
| 2:45 | Unfamiliar ticker | Search PIDILITIND (outside the portfolio) → opens with its documents; APPLE → honest "couldn't find" |

## Backend fixes (A and B)

* **A, CIPLA** (`backend/app/services/filing_facts.py`):
  * Names stop at form-field words, so "Reason" from the Reg-30 table header no longer joins the name.
  * Any C-suite title is read verbatim ("Global Chief Technical Operating Officer").
  * The disclosure-form boilerplate "Reason for change viz. appointment, resignation, removal, death or otherwise" no longer decides the action (a new test exposed it reading as *resignation*).
  * The sentence never uses a placeholder like "the role".
  * Re-analysed CIPLA. Stored card: "Vikram Janakiraman appointed as Global Chief Technical Operating Officer (from 7th December, 2026)".
  * A read-only scan of every stored management change showed CIPLA was the only record affected.
* **B, INFY** (`backend/app/services/guidance.py`, `NOT_GUIDANCE`):
  * Share-of-revenue statements ("accounts for 10.5% of our revenues", "10.5% of total revenue") are not guidance.
  * Neither is a single deal's contribution to growth ("contribute over 1% revenue growth", "1% revenue growth outlook from that one deal").
  * Re-extracted all 95 transcripts: 412 → 404 rows. Removed: INFY 10.5% "revenue growth", INFY "8–9% of the revenue" (share filed as growth), three INFY deal-contribution items and one BHARTIARTL analyst question.
  * The Infosys Promise Ledger now has no 10.5% or deal-contribution items. The verified FY26 margin comparison (20–22% → 20.92%, met), the FY27 revenue cut (1.5–3.5% → 1–2%) and the Handover guidance-cut item are unchanged.
* **Tests:** 3 new regression tests built from the real filing and transcript sentences. Backend **155 passed**.
* **Not changed:** prompts (rules only, so no `*_PROMPT_VERSION` bump), API contracts and schema.

## Backend fix (ASIANPAINT, P1)

`backend/app/services/filing_facts.py`:
* Sentences recounting an earlier intimation ("vide its letter … had informed") are history. They no longer set the action or approval status.
* "Shareholders … have approved" / "resolution … passed" marks approval obtained, which overrides a quoted "subject to approval". The approved role is recorded (`approved_role`).
* A departure phrase whose object is someone else ("upon completion of the term of Mr. R. Seshasayee") is ignored for the lead person. "assume office" counts as an appointment.
* Sentence splitting no longer breaks after "Mr." or initials.
* A read-only scan of all 6 stored management changes showed only ASIANPAINT changed. Re-analysed; no reviews used the old event key. The CFO-departure plan no longer matches it.
* Regression tests: the real ASIANPAINT letter, plus a genuine cessation and a genuine pending approval (to ensure the fix does not over-correct). Backend **157 passed**.
