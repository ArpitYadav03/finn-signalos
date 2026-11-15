# AI build history

This project was built in one session with **Claude Code** (Anthropic, model Claude Opus 5.5) acting as the
coding agent, driven by a single long master prompt (the full brief, product vision, stack and milestones).
This file summarises what the agent did and where it had to correct itself. It is a summary, not a transcript.

## Where the real conversation is

* Claude Code session transcript: export it from the Claude desktop app (session menu → export) or from
  `~/.claude/projects/…/*.jsonl`, and link/attach it here: **[add link before submission]**
* Any ChatGPT/Codex conversations used for planning: **[add links if any]**

## Timeline of agent work

1. **Recon.** Read both ENAM PDFs from `~/Downloads`, checked toolchain (Python 3.12, no Node, Docker present),
   probed sources: Yahoo ✓, Screener ✓, NSE ✗ (403), no LLM key. → Decided on deterministic-first AI (ADR-001)
   and Screener as document index (ADR-002).
2. **Source probing before coding.** Parsed a real Screener page to confirm filing timestamps are ISO with IST
   offset and that links resolve to BSE PDFs; fetched a real TCS Q2 FY27 transcript to design the speaker
   parser and guidance rules against real text.
3. **Backend core.** Models, deterministic finance/period functions, classifier, explainable scorer, polite
   HTTP client, parser, pipeline, filing workflow, retriever, research agent, alerts, API.
4. **First real ingestion run** (18 companies): 810 price bars, 65 filings. Two bugs surfaced and were fixed:
   duplicate document URLs inside one listing (unique-constraint failure) and BSE's CDN rejecting a user agent
   containing parentheses (switched to a plain honest UA — no spoofing).
5. **Reviewing outputs on real data led to product fixes:**
   * Maruti's card was driven by *Suzuki Motor's* results filing → new rule: "results issued by <other entity>"
     is a related-entity press release.
   * "Board Meeting Outcome" for a warrant issue was classed as results → ambiguous titles now defer to the
     listing subject.
   * A ₹1.6 lakh telecom penalty ranked *high* → size-aware materiality (amount vs annual revenue), per-share
     prices excluded from deal sizes.
   * "We exited FY26 at 25%" was extracted as a promise → statements about already-ended periods are dropped.
   * An analyst's "is the 25% target intact?" looked like guidance → speaker-aware chunking with analyst
     detection from moderator introductions.
   * One sentence with two commitments was merged into one → clause-level extraction (caught by a unit test).
   * Infosys transcripts use name-on-its-own-line speakers and were attributed to "Membership No" from the
     cover letter → second speaker convention + label sanity rules.
   * Infosys revenue guidance compared against INR growth → constant-currency basis inferred from the call
     and comparison refused; segment-level guidance refused against company totals.
   * Promise Tracker showed 33 near-duplicate cards → reiterations grouped (16 cards), revisions linked.
6. **Frontend.** Next.js 15 + Tailwind v4 + Recharts; hand-built components instead of shadcn CLI (no
   network-heavy generator needed, smaller surface). Fixed a hydration mismatch (locale date) and lint errors.
   Node 22.11 needed `--experimental-require-module` for Tailwind's ESM-only dependency (documented in
   `scripts/dev-frontend.sh`).
7. **Verification.** 53 pytest tests; eval harness (`evals/scripts/run_evals.py`) with measured results;
   manual browser checks at 375 px width of Briefing → Investigate → Evidence → Promises → Alerts.

8. **Iteration 2 — conversational agents (user request with screenshots).** The user asked for an AI agent on the
   Alerts page and chat in Research ("what caused this?", "latest news"). Screenshots also exposed bugs, fixed first:
   * Bank headline showed Screener's "financing profit" (−₹5,565 cr for HDFC Bank, distorted by insurance subsidiaries)
     → lenders now show net interest income computed as interest earned − interest expended.
   * "Filing has no extractable text" appeared for a filing that *was* indexed (keyword search simply found nothing)
     → fall back to the filing's own passages; quotes skip letterhead/sign-off boilerplate.
   * "Latest news" pulled IMF commentary from a transcript → new intents route news to the company's exchange filings
     and price questions to EOD moves + filings around those dates.
   Then built: shared agent runtime, grounding checks (citation + number verification), research chat with history,
   alerts agent with typed proposals and an apply endpoint, chat thread UI, assistant panel. Tested the agent loops
   with a scripted fake LLM (fabricated citation stripped, invented ₹ figure flagged, invalid proposal self-corrected).

9. **Iteration 3 — going live with Gemini.** Created `.env` (git-ignored, owner-only permissions); the user added a
   Google AI Studio key. A pasted AI Studio `curl` snippet had landed in `LLM_BASE_URL`; the file was rebuilt
   programmatically without ever printing the key. Found and fixed: a blank `DATABASE_URL=` crashed startup;
   `gemini-2.5-flash` returned 404 for chat, so models were chosen by listing and testing what the key supports
   (`gemini-flash-lite-latest` fast tier, `gemini-3.5-flash` reasoning tier; `gemini-pro-latest` hit free-tier 429s).
   First live run exhausted the step budget → added a forced final answer with tools disabled and a "≤3 tool rounds"
   instruction (45 s → 8–21 s). Grouped citations "[E8, E14]" are now split into clickable chips; the answer renderer
   handles a heading directly above a bullet list.

10. **Iteration 4 — "AI synthesis unavailable".** Logs showed Gemini 429s: the free tier's per-model *daily* quota on
    `gemini-3.5-flash` was exhausted (provider said retry in ~5 h). Added a per-tier fallback chain with quota-aware
    cooldowns (429 → skip the model until the provider's retryDelay; 5xx/timeout → 60 s; 404 → 24 h), so another model
    answers immediately; the badge shows the model that actually answered. Research tools now re-check the filing index
    at the source when FINN's copy is >15 min old and fetch missing EOD sessions; the prompt carries the current IST
    time and market status. Unit tests cover the fallback chain.

11. **Iteration 5 — structured review (9 areas).** Audited against live data before editing, then fixed in impact order:
    event dedup by person/action identity (HDFC Rangan RBI + board filings → one event, both sources); fact-based
    leadership materiality (continuity vs disruption; retirement vs resignation; shareholder ratification of a new
    MD & CEO); names with apostrophes/initials and roles near the person (not the signature block); size-aware "why";
    primary-source company profile quoted from the annual report (Screener "About" removed from Research);
    filing citations resolve to filing passages; Promise Ledger completed comparison (Infosys FY26 operating-margin
    guidance 20–22%, stated Apr 2025 and reiterated thrice → FY26 derived 20.92%, met; CFO's own "full year margins
    is at 21%" cross-checked from the Apr 2026 call); alert evaluation log + "Evaluate now"; stale price bar detection;
    sparkline semantics; Evidence Lens passage stepping + whole-page view; contrast and type-size fixes.
    New regression tests caught two real bugs: generic-title filings left unclassified without an LLM, and dedup
    silently failing within one ingestion run (analysis linked by FK only, invisible to the in-memory session).

12. **Iteration 6 — daily context.** The user suggested loading relevant documents every day. The daily job now also
    indexes recent non-routine filings' text, newly published transcripts and the latest annual report (budgeted,
    idempotent), and an in-process scheduler runs it at 08:30/18:30 IST with a startup catch-up; Briefing and Alerts
    show last/next refresh and a "Refresh now" button. Fixed a false "figures not found" warning caused by JSON
    escaping of "₹" in tool outputs.

13. **Iteration 7 — product polish against the ENAM brief.** Rebuilt the Briefing (real-count summary, custom
    date range, collection status, Event DNA per filing), Portfolio (counts, equal-weight disclaimer, holdings
    management), Research (legend for facts / calculations / interpretation, limitations, follow-up chip, session
    thread, failure keeps the question), Evidence (desktop drawer, focus return, figures-and-calculations view that
    opens the results filing at the page containing the figures), Promise Ledger (commitments / aspirations / other)
    and Alerts (Describe → Review → Save → History, preview with AND/OR, unsupported parts named before saving,
    evaluation status, threshold editing). Verifying five journeys in the browser found five real problems, all fixed
    with tests: approval and allotment of the same warrants shown as two events; the results link landing on the cover
    letter; an advertising line chosen as Pidilite's "business description"; the alerts model intermittently adding an
    unfiltered duplicate that bypassed a CEO/CFO filter (ADR-011); an empty Promise Ledger implying "no guidance" when
    the company has no transcripts. Details: `docs/product-polish.md`.

14. **Iteration 8 — financial correctness.** Reproduced four review findings first (Titan CP redemption shown as a
    raise; ASIANPAINT annual cash conversion opening quarterly figures; a validator blind to sign/unit/period; causes on
    data citations), plus two found while reproducing (different CP redemptions merged by a date-less group id; a
    dangling saved research question). Added capital-event parsing (instrument, action, direction, stage, amount,
    evidence), claim-level references with formulas and inputs, primary-page matching for annual figures, a semantic
    validator, causal-claim checks and one financial formatter. 18 new regression tests. Details:
    `docs/financial-correctness.md`, ADR-012.

15. **Iteration 9 — reliability, data history, evaluation.** Durable leased ingestion jobs (retry/backoff, terminal
    errors with explanations, stage progress, restart recovery), versioned financial snapshots/observations with
    validation, honest coverage boundaries for the limited filings source, session-aware event timing, a server-side
    proxy + workspace key for the public boundary, and a held-out evaluation. Live verification found three real bugs:
    every financial snapshot rejected over half-year period labels; 403 responses retried instead of respected; bullet
    labels hidden from the claim validator. Retrieval variants were benchmarked and none adopted. Details:
    `docs/reliability-and-evaluation.md`.

16. **Iteration 10 — one investor workflow.** "Since your last review" with explicit review actions, lean cards,
    next-evidence monitoring (suggested, not alerts), Event DNA ↔ Promise Ledger links under strict comparability rules,
    concise labelled research answers with a figures table, and duplicate-aware alert proposals. Building the demo
    exposed guidance-extraction errors on real transcripts (history, H1 actuals, R&D share of sales, truncated
    sentences) — fixed with regressions and re-extraction. Details: `docs/investor-workflow.md`.

17. **Iteration 11 — Handover, Competing Hypotheses, Replay.** Correctness first: building on real data exposed NCD/CP/QIP
    allotments read as repayments ("redeemable"), live AND between price and volume being ignored, a volume ratio computed
    from 16 sessions while labelled 20, and repeats reopening reviewed events — all fixed with regressions. Then saved
    monitoring plans with a return briefing, cash-conversion investigations with quote-checked evidence (no verdicts), and
    a durable point-in-time replay that reuses the live evaluators. Details: `docs/handover-hypotheses-replay.md`, ADR-013.

18. **Final frontend pass (15 Nov 2026).** Review fixes (replay date race, contextual Investigate with a route back, Digest / My plans / Unreviewed views, comparability presentation), light coral visual system, workspace sign-in screen on the existing password gate, truthful freshness labels and provenance copy. Re-verified known issues: CIPLA and INFY extraction defects still present (backend). Status NOT READY; see `docs/submission-status.md`.

## Things deliberately not claimed

* Research chat and the alerts agent have run live against Gemini; LLM filing polish, LLM guidance extraction and
  embedding retrieval are wired but have not been evaluated on real data.
* No deployment was performed.
