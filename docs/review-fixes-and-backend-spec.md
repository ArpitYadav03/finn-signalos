# Review issues: frontend fixes and backend repair specification

Date: 15 Nov 2026 · Branch `fix/frontend-review-issues` · The backend was frozen: no backend code, prompts, data,
stored analyses, ingestion or API contracts were changed. Every frontend change uses fields the API already returns.

## 1. Reproduction

| # | Issue | Reproduced? | Evidence |
|---|---|---|---|
| 1 | ASIANPAINT card contradicts its source | **Yes** | Announcement 98 (6 Nov 2026): stored `action: "cessation"`, `shareholder_approval_pending: true`, `effective: "23rd January 2027"`, headline "Leo Puri ceases as Chairperson", `filing_role: "Shareholder approval"`, `event_key: mgmt:puri:exit`. Filing text: shareholders **approved** his appointment as Independent Director; he **assumes** office as Chairman from 23 Jan 2027 on completion of R. Seshasayee's term |
| 2 | L&T counterevidence not comparable | **Yes** | Investigation 1, version 1: passage `doc:33021` "Cash Flow from Operations excluding Financial Services … during the quarter" stored as *H1 supports / H2 challenges* against FY22–FY26 consolidated annual figures. The stored version predates the `period_mismatch` flag, and **no** business-scope field exists for passages |
| 3 | Indexed-mode cutoff missing for guidance | **Yes (code audit)** | `backend/app/services/replay.py:145`: the guidance branch calls `guidance_cuts(..., as_of=d)` with no `mode` check; `management_guidance` has no ingestion/availability timestamp |
| 4 | Replay start date overwritten | **Yes** | `Replay.tsx` set start and end unconditionally when `/status` replied. Coverage was stored without its inputs, a mode change did not invalidate it, and Run posted the current inputs rather than the checked ones |
| 5 | Handover Investigate loses context | **Yes** | `Handover.tsx` linked to `/research?ticker=X` only; the existing route already supports `filing`, `q` and `tab` |
| 6 | Homepage mixes plans, unreviewed and digest | **Yes** | One page stacked the plan briefing, the 14-day review feed and the 24-hour digest, so the same Bajaj event appeared up to three times. Two plans named "Holdings: Price moves · Volume spikes" were indistinguishable |

## 2. Frontend corrections (implemented)

* **Replay form (issue 4)** — `frontend/lib/replayForm.ts` (pure reducer) + `components/Replay.tsx`:
  * Server defaults fill only fields the user has not touched, whenever they arrive.
  * Coverage is stored with its key (plan, version, start, end, mode). It is hidden as soon as any of those change, with the note "Inputs changed since the last check". A late reply for an older key is dropped.
  * "Checked: 2026-09-08 to 2026-11-13 · rule v1 · filings counted when published" is shown before Run, and Run posts exactly those checked values plus the plan version.
  * Busy state blocks double submits, range errors appear inline, and the limitations of a run are always visible (no longer collapsed).
* **Contextual Investigate (issue 5)** — `frontend/lib/researchLink.ts`:
  * Handover's Investigate passes `filing` (the item's latest filing), `q` (the existing category question) and the ticker. Investigation items open `tab=evidence`.
  * `from` carries the plan briefing and item anchor. Research shows "← Back to briefing", and the briefing scrolls back to that item.
  * `from` is accepted only as an in-app path (no `//`, schemes or backslashes).
* **Briefing views (issue 6)** — `frontend/lib/briefingView.ts`, `app/page.tsx`, `components/ReviewFeed.tsx`, `components/Handover.tsx`:
  * Tabs **Today** (default; the unchanged 24-hour digest with its window controls), **My plans** and **Unreviewed**, selected by `?view=` (`?plan=` implies My plans). Switching only changes the URL.
  * Unreviewed shows each event once across its sections, and first-review items older than 24 hours are labelled "Older item · filed N days ago, not reviewed yet".
  * Plan chips and the plan header carry a subtitle built from the stored spec: "All 18 holdings · falls >4% or volume >2.5×" vs "… falls >5% and volume >2×".
  * The source-status note stays visible in every view, and coverage gaps remain in Today and in each plan's *Unable to evaluate*.
* **Hypothesis presentation (issue 2, presentation only)** — `components/Hypotheses.tsx`:
  * Evidence is split into *Computed from annual statements · FY22–FY26 (full years), consolidated* and *Passages from the company's documents*, and counts are shown per source kind.
  * Each passage shows "Passage: earnings call, Jul 2026 · Computed figures: FY22–FY26 (full years), consolidated", taken from structured fields only. Stored limitations sit beside each item, and `period_mismatch` is shown when present.
  * A group note states that FINN does not record a passage's business scope.
  * The counts note says counts are not evidential strength.
  * **The relation labels themselves are unchanged; this is not a fix.**
* **Replay limitation (issue 3, presentation only)** — in indexed mode with a guidance condition, coverage and results state that guidance is matched by statement date, not indexing date. **Not a fix.**

## 3. Verification

* Frontend regressions: `npm test` runs Node's built-in test runner (no new dependency). **11 pass**:
  * Date race: typed before defaults; late second reply.
  * Stale coverage: date, mode or plan-version change, and same inputs again.
  * Range validation.
  * Contextual link: filing, question, company and return path; investigation tab; unsafe return targets rejected.
  * View selection, cross-section de-duplication, older-item labels, distinct plan subtitles.
* `tsc --noEmit`, `next lint` and `next build` are clean.
* Browser at 390 px (real data):
  * With `/status` delayed 3 s, a typed 2026-09-08 start survived and only the end defaulted to 2026-11-13.
  * Checking coverage then switching mode hid the coverage and Run and showed the stale note; switching back restored them.
  * Indexed mode showed the guidance gap note.
  * Briefing defaults to Today. Unreviewed shows "Older item · filed 8 days ago". My plans shows four distinguishable chips.
  * Investigate on Bajaj opened Research with the 11 Nov QIP filing in context and the question in the URL; Back returned to My plans with the item at the top.
  * The L&T rows show the period/scope line.
  * `event_reviews` stayed at 0 after browsing every view.
* Screenshots: `docs/screenshots/r01-replay-checked-dates-390.jpg`, `r02-lt-comparability-390.jpg`, `r03-briefing-views-plans-390.jpg`.
* Not done: a desktop screenshot this round, a screen-reader pass, and an end-to-end browser test framework (the regressions cover the pure logic the components use).

## 4. Backend repair specification (prioritised)

### P1 — Management-change extraction (issue 1): wrong facts reach users

Where: `backend/app/services/filing_facts.py:management_facts`, `action_of`, `role_near`; `agents/filing_agent.py`
(headline / what_changed); `services/milestones.py` (pending approval milestones).

1. **Person–role–action association.** Pair each action with the person in its own clause, not the lead person plus a document-wide action word. "upon completion of the term of Mr. R. Seshasayee as Chairman" is Seshasayee's term ending (a scheduled cessation), not Puri's. Emit `changes: [{person, role, action, effective, status}]` instead of single `people/role/action` fields; keep the old fields as a derived projection so the API contract is unchanged.
2. **Appointment vs departure.** "assume office as Chairman" / "approved the appointment" are appointments. `cessation` requires a departure verb whose subject is that person.
3. **Approval status.** A result letter ("based on the voting results … the shareholders have approved") sets `shareholder_approval: obtained` and overrides a quoted earlier "subject to the approval of the shareholders". That quote belongs to the referenced 7 Sep letter (history) and must not set `pending`. `filing_role: "Shareholder approval"` and `pending: true` together must fail validation.
4. **History vs current event.** Sentences introduced by "vide its letter … dated …", "had informed" or "earlier intimation" are history and contribute nothing to the current action or status.
5. **Announcement vs effective date.** Store `announced_at` (filing time) and `effective_from` per change. 23 Jan 2027 is Puri's future effective date as Chairman, not a cessation date. Headlines use future tense for future effective dates ("will become Chairman from 23 Jan 2027").
6. **Reanalysis.** Bump `FILING_PROMPT_VERSION` and the extractor version, then re-run `python -m app.cli reanalyse --ticker <T>` for every `management_change` analysis. Report changed headlines and facts before/after. Recompute `event_key` (currently `mgmt:puri:exit`) and reconcile `event_reviews` keyed on old keys (map old → new or reopen with a note). Re-evaluate plans that use CFO/CEO departure conditions.
7. **Regression (real filing).** A test fixture with announcement 98's text must assert:
   * changes = [{Leo Puri, Independent Director, appointment, approved by shareholders}, {Leo Puri, Chairperson, appointment, effective 2027-01-23}, {R. Seshasayee, Chairperson, term ends 2027-01-22}];
   * `shareholder_approval_pending` is false, the headline contains no "ceases", and a "CFO/Chair departure" plan condition does not match Puri;
   * no "Shareholder approval" milestone is open.

Acceptance: the real-text regression passes; reanalysis of all management changes yields zero records where `filing_role` says approval was obtained while `pending` is true; a manual check of 10 random management-change cards against their PDFs.

### P2 — Hypothesis compatibility and support validation (issue 2): misleading relations

Where: `backend/app/services/hypotheses.py` (`passages`, `summarise`), `llm/prompts.py`.

1. **Structured comparability metadata per passage** (new optional fields; additive, backwards compatible): `period_type` (annual / half_year / quarter / point_in_time / unknown) and `period_label`, derived from the document's reporting period and, when the quote names it, the period in the quote. Also `business_scope` (consolidated / standalone / excluding_segment / segment / unknown), with the segment named when stated.
2. **Compatibility gate before a relation counts.** A passage may *support* or *challenge* only when `period_type` and `business_scope` are compatible with the computed evidence (annual, consolidated). Otherwise its relation is stored as `context` and it gets `incompatible_reason`, e.g. "quarterly; excludes Financial Services". Counts and the summary state exclude gated items.
3. **Semantic support validation.** Besides the verbatim-quote check, reject a model relation when the quote has no claim about the hypothesis's mechanism (working capital, receivables, inventory, advances or cash conversion). For relations that rest on a figure, require the figure to come from the computed evidence.
4. **Re-run** saved investigations so a new version records the change. Show it via the existing "evidence changed" flow; never edit stored versions.
5. **Frontend dependency.** Once `business_scope`, `period_type` and `incompatible_reason` exist, the passage line shows them explicitly instead of the generic scope note.

Acceptance:
* L&T `doc:33021` becomes `context` with "quarterly; excludes Financial Services", and the summary state is recomputed without it.
* A synthetic annual consolidated passage still counts.
* The SUNPHARMA table-row discard still holds.
* Tests cover each `business_scope` value.

### P3 — Replay availability cutoff for guidance (issue 3): wrong in one mode

Where: `models.ManagementGuidance`, `services/guidance.py` (extraction), `services/monitoring.guidance_cuts`, `services/replay.py:145`.

1. **Availability timestamps.** Add `available_at` (when the source document was published) and `indexed_at` (when FINN extracted it from the document, i.e. `documents.created_at` / extraction time) to `management_guidance`. Backfill from the source document. Mark rows whose document was re-fetched after extraction as `indexed_at_uncertain`.
2. **Both statements in a comparison.** A cut on session D is reported only if the *current* statement **and** the *previous* statement it is compared with were available by D under the chosen mode (public: `available_at`; indexed: `indexed_at`). Pass `mode` into `guidance_cuts(..., as_of, mode)`.
3. **Consistent enforcement.** Use one helper `available_by(row, d, mode)` for filings and guidance. A cut whose statement became available on a non-trading day rolls into the next session, as filings already do.
4. **Current analysis and revised documents.** Keep the disclosure that classifications and extractions are today's. If a document's `content_hash` changed after D, flag dependent guidance as "document revised after replay date; earlier version unavailable" and exclude it in indexed mode. Never present replayed model output as a reconstruction of what FINN produced then.
5. **Regression.** A guidance statement published (call date) before the replay end but indexed after it:
   * public mode reports the cut on the session after publication;
   * indexed mode reports it only on or after the session FINN indexed it, or not at all if that is after the end;
   * a replay ending before either date shows nothing.
   * Also a previous-statement case: the current statement is available but the earlier one is not yet indexed, so no cut is reported.

Acceptance: the tests above, plus the frontend's indexed-mode gap note is removed once `mode` is honoured.

### P4 — Smaller backend follow-ups found during this review

* Handover Investigate uses the latest filing id of the event; an API field for "the filing that matched" would let Research highlight the exact matching filing in multi-filing events.
* `/api/v1/investigations/compare` returns no `period_mismatch` for versions saved before the flag existed. Re-running saved investigations (P2.4) also fixes this.

## 5. Unresolved (explicit)

1. **ASIANPAINT card is still wrong.** Its stored facts are unchanged (cessation, approval pending, wrong date semantics). Backend P1.
2. **L&T quarterly / ex-Financial-Services passage still counts** as supporting H1 and challenging H2 in stored results and summary state. Only its presentation changed. Backend P2.
3. **Indexed-mode replay can show guidance cuts before FINN indexed them.** It is now disclosed in the UI, not fixed. Backend P3.
4. Business scope of passages is not recorded anywhere, so the UI can only show a generic note (dependency on P2.1).
5. No end-to-end browser test framework. The regressions test the pure logic the components use, and journeys were verified manually in the browser.
6. Not done this round: a desktop screenshot of the new views and a screen-reader pass.
