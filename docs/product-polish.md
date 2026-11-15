# Product polish pass — decisions, verification, limitations

Date: 15 Nov 2026 (Sunday). All checks ran against the live local stack (FastAPI on :8000, Next.js dev on :3000)
with real data: 18 holdings, filings from the exchange index, Yahoo end-of-day prices, Gemini for AI answers.
Screenshots are in [`docs/screenshots/`](screenshots/) (`m*` = 390 px mobile, `d*` = 1440 px desktop).

## What changed

| Area | Change |
|---|---|
| Visual system | Shared `.btn-primary / .btn-secondary / .btn-quiet`, `.card`, `.label` tokens; true minus signs and `0.0%` unsigned; tabular numerals; readable dates (`Fri, 13 Nov`) instead of ISO; model and tool details moved into collapsible **Analysis details** everywhere. |
| Briefing | Summary line from real counts ("4 developments to review · Across 18 holdings · Previous 7 days"); presets plus a **Custom** from/to range (validated, timezone-aware on the server); collection status distinguishes "nothing material" from "some sources unavailable"; quiet-window state says how many filings were checked and offers a wider window; desktop uses a two-column layout (events + context). |
| Event cards | Company · type · priority, headline, what changed, figures table with period and unit, why it matters, **Event DNA** ("What each filing added": Board decision → Completed, Regulatory approval → Board decision, Advance notice → Repeat disclosure), filing time, filing count, same-day stock move; **Investigate** (primary) and **View evidence** (secondary); details on demand. |
| Grouping | New fund-raise identity (instrument + unit price/count): Bajaj Finance's warrant approval (12:12 IST) and allotment (12:23 IST) are now one event; its QIP stays separate. |
| Portfolio | Session date, up/down/unchanged/unavailable counts, equal-weight average labelled as **not your investment return**; sorting keeps "unavailable" last; volume shown as "1.1× vol"; footer explains sparkline period, volume basis, events vs filings; **Manage holdings** (add/remove, input kept on failure). |
| Research | Clear active company ("Asking about …"), filing-in-context card, suggested questions that differ for lenders, a legend separating **quoted document facts / FINN-calculated figures / interpretation**, explicit "Insufficient evidence" block, **Limitations** list, a one-tap follow-up question, **New conversation**, thread kept per company for the browser session, failures keep the question with Retry, plain "couldn't find this symbol" state, explicit "no price loaded" state. |
| Evidence | Bottom sheet on mobile, right-hand drawer on desktop (answer stays readable), focus moves to Close and returns to the opening control without scrolling, safe-area padding. New **Figures & calculations** view: current / previous / year-ago inputs, QoQ and YoY, formula, basis, unit, the company's results filing (opened at the page whose text contains the reported figures, e.g. ITC p.9 consolidated statement), and the standardised table labelled as a secondary source. |
| Financials | Lender-aware labels (interest earned; no debt/equity or cash conversion for banks), chart with text alternative and a "show as table" option. |
| Promise Ledger | Sections: dated **commitments** (scored), **long-term aspirations** (not scored), **other statements** (collapsed); plain-language status and explanations; promised vs reported with period, basis, gap; history of revisions and repeats (deduplicated by call); empty state distinguishes "no transcripts to read" (ITC) from "read N transcripts, found no targets". |
| Alerts | Describe → Review → Save → History stepper; the review shows scope, each condition with AND/OR spelled out, priority filter and check schedule; unsupported parts (intraday, RSI, price levels, news…) are named **before** saving; saved rules show Active/Paused, a readable sentence, last evaluation (matched / not matched / unable to evaluate, with reasons and session), **Edit threshold** (saves and re-checks), delete with confirmation; History shows which rule raised each alert; global thresholds moved to a separate **Briefing** tab that says it does not create alerts. |

## Product decisions

1. **The page decides what to say, not the model.** Summary lines, statuses, counts and empty states are computed from
   data; the model writes only answer prose and is labelled as interpretation where it is not cited.
2. **Unknown is never zero.** A repeated provider bar is "unavailable", a missing price says so, an empty Promise
   Ledger says whether transcripts existed, a filings-only alert says "filings up to …" rather than implying a close.
3. **Deterministic guards on agent proposals** ([ADR-011](decisions/ADR-011-deterministic-guards-on-agent-proposals.md)).
   In one live run the alerts model proposed "any leadership change **OR** a CEO/CFO change", which would have
   bypassed the role filter. A rule-based tightening step now applies roles and rating direction named in the
   user's words and drops broader duplicates, whatever the model proposes.
4. **Primary sources stay primary.** The standardised results table is shown as a secondary aggregation, beneath the
   company's own filing; profiles are quoted from the annual report or omitted (Pidilite's best rule-based
   candidate was an advertising line, so the pattern was tightened and the app now shows "no description yet").
5. **Desktop width is for context, not bigger cards**: Briefing gets a context column; evidence opens beside the
   answer; the Promise Ledger uses two columns.

## Verification

Automated: backend `pytest` **91 passed** (13 new tests this pass, up from 78: Event DNA roles, custom window bounds and 422,
portfolio counts, holdings add/remove, role-filtered and downgrade alerts, unsupported conditions + preview,
threshold edit re-evaluates, follow-up extraction incl. offer phrasing, ledger sections/explanations, results-page
targeting, fund-raise identity, filter tightening, ledger coverage). Frontend `tsc --noEmit`, `next lint` clean,
`next build` succeeds.

Manual, in the in-app browser (390×844 mobile emulation unless noted):

| Journey | Result | Evidence |
|---|---|---|
| 1. Briefing → grouped event → evidence → contextual research | ✅ 7-day window shows Bajaj warrants as one event with Event DNA; View evidence opens the allotment filing at page 1 of 3; Escape returns focus to the button with scroll position unchanged (318.5 px before and after); Investigate on HDFC Bank opens Research with the filing in context and a cited answer. I checked two claims against source text: "51.23%" is in the allotment filing; the Anup Bagchi MD & CEO appointment is in the postal-ballot outcome (p.3–4). | m02, m03, m04, m05 |
| 2. Portfolio → company → financial metrics → supporting source | ✅ Portfolio counts (3 up · 14 down · 1 unchanged) and equal-weight note; ITC → Financials → inputs & source → calculation view; Revenue QoQ (20,269 − 19,114) ÷ 19,114 = +6.0% and PAT YoY (4,229 − 5,187) ÷ 5,187 = −18.5% re-derived by hand; "Read the filing" opens the BSE results filing. | m06, m07, m08 |
| 3. Company outside the demo → lookup → research or honest limitation | ✅ PIDILITIND resolved live in ~30 s with progress steps, then answered "What's the latest" with cited filings and calculated figures (20.93% YoY revenue re-derived). ZZNOTREAL shows a plain not-found explanation. | m09 |
| 4. Alert description → preview → saved rule → evaluation status | ✅ "CEO or CFO change … or RSI crosses 70 intraday" → review with one CEO/CFO condition, scope, schedule and an unsupported-parts warning → saved → status "Not matched · filings up to …". Edit threshold on a temporary TCS rule (0.3× → 3×) re-checked against the 13 Nov close and flipped Matched → Not matched; delete with confirmation worked. Test rules were removed afterwards; the one test alert remains as *dismissed*. | m10, m11 |
| 5. Quiet window, missing data, recoverable failure | ✅ 24 h window on a Sunday: "Nothing material to review · 1 filing was published; none rose above routine · last refresh completed for every holding". Missing data: ITC ledger now says no transcripts exist; Pidilite shows "No end-of-day price loaded". Failure: with the backend stopped the question stayed in the box with Retry; recovery was then verified in a separate tab by failing only the research request and pressing Retry after restoring it → cited answer, error cleared. | m01, m12 |
| Desktop 1440 px | ✅ Two-column Briefing, evidence drawer beside the answer, two-column Promise Ledger; no horizontal scroll. | d01, d02, d03 |
| Layout checks | ✅ `scrollWidth == clientWidth` (390) on Briefing, Research, Portfolio, Alerts; bottom nav hides while a text field is focused (seen during the alert review). New controls are sized 36–44 px; I did not measure every element. | — |

### Not verified (not claimed)

* Screen-reader output, a measured contrast audit and real-device safe areas (only emulation; the CSS uses
  `env(safe-area-inset-bottom)`).
* `prefers-reduced-motion` behaviour was not toggled during this pass (rule exists in `globals.css`).
* Desktop screenshots of Portfolio and Alerts were not captured (I stopped driving the browser pane once you
  started using it).
* The first in-pane Retry after a real backend restart did not reach the server (the page re-mounted when I
  hot-edited it); recovery was verified by the simulated-failure run above instead.
* Some in-pane screenshots taken right after programmatic scrolling were stale; those were discarded and the state
  was checked through the DOM instead.

## Remaining limitations

* Filing feed sees ~5 recent filings per company from the index, so older results filings (TCS, HDFC Bank,
  Infosys Q2 FY27) can't be linked from the calculation view; the view says so.
* Incoming-executive background comes only from the filing text; no LinkedIn or web enrichment (primary-only by
  design, ADR-010).
* Rule-based profile selection is weak when no model is configured; with the model it selects, never writes.
* Event identity covers leadership, ratings and capital raises; other multi-filing events rely on text similarity
  and a 3-day window.
* Not deployed; no Playwright E2E suite in CI (manual browser checks above plus API-level tests).

## ENAM requirements still unmet or partial

| Requirement | Status |
|---|---|
| Public deployment URL | ⚙️ deployment-ready (Docker, Render/Vercel/Supabase steps), **not deployed** |
| Push notifications outside the app | ⬜ in-app alerts only |
| Management profile enrichment beyond the filing | 🟡 filing-only |
| Narrative drift across calls | ⬜ not built |
| Evals with recorded LLM cost/latency on the full set | 🟡 harness exists; AI-mode run not recorded |
| Submission email | ⬜ not sent (needs your explicit instruction) |
