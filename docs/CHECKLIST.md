# Development checklist

Legend: ✅ done & verified · 🟡 partial · ⚙️ implemented, not exercised · ⬜ not started

## Milestones
- ✅ M0 Architecture, schema, API contracts, adapters, ADRs
- ✅ M1 Runnable foundation (FastAPI, Next.js, SQLite/Postgres, Alembic, health/ready, tests)
- ✅ M2 Market data + portfolio (18 holdings, EOD, returns, volume ratio, freshness)
- ✅ M3 Filings + Signal Radar (ingest, dedupe, classify, score, group, cards, 24h/3d/7d/30d)
- ✅ M4 Primary-source RAG (any ticker, PDF → pages → chunks, hybrid retrieval, cited answers)
- ✅ M5 Promise Tracker (real guidance, period/basis matching, revisions, refusals)
- ✅ M6 Alerts (price/volume/category rules, NL parser, enable/disable/delete, dedupe, history)
- 🟡 M7 Polish & evaluation (evals measured; LLM-mode evals pending a key)
- 🟡 M8 Deployment (Docker, compose, CI, cron workflow, docs done; actual deploy pending credentials)

## Features
- ✅ Signal Radar · ✅ Evidence Lens · ✅ Promise Tracker · ✅ NL alert builder · ✅ 5-minute brief (Briefing)
- ✅ Research chat (multi-turn, filing-aware) — deterministic mode verified in browser; AI mode tested with scripted fake LLM
- ✅ Alerts assistant (agent with confirmable actions) — rule-based mode verified in browser; AI mode tested with scripted fake LLM
- ✅ Real-model runs (Gemini): research chat (single + follow-up), alerts agent — verified 15 Nov 2026
- ⚙️ Not yet evaluated on real data: LLM filing polish, LLM guidance extraction, embedding-based retrieval at scale
- 🟡 Bull vs bear (extractive passages only)
- ⬜ Narrative drift detector · ⬜ Web push notifications · ⬜ Playwright E2E (manual browser checks done instead)

## Review round (15 Nov 2026)
- ✅ Event dedup (entity/action identity + subject similarity), one card per event with all sources
- ✅ Fact-based leadership materiality, size-aware "why it matters", explainable reasons incl. grouping line
- ✅ Research: primary-source company profile (annual report quote + page); filing citations at passage level
- ✅ Promise Ledger: completed FY26 comparison (Infosys) with management-reported cross-check; period parsing fixed
- ✅ Alerts: per-rule and global last-evaluated timestamps, Evaluate now
- ✅ Data: stale-bar detection, sparkline = 20-session trend (labelled), portfolio counts events, NII for banks
- ✅ Evidence Lens: period/page metadata, previous/next passage, whole-page view with highlight
- ✅ Mobile: contrast ≥4.5:1 for muted text, ≥11px text, chat padding, wider touch targets, scrollable result tables
- ⬜ Playwright E2E in CI (API-level Push→Pull regression test added instead)

## Product polish (15 Nov 2026) — see docs/product-polish.md
- ✅ Briefing: real-count summary, custom range, collection status, quiet state, Event DNA, desktop context column
- ✅ Portfolio: counts incl. unavailable, equal-weight disclaimer, sort with unavailable last, manage holdings
- ✅ Research: facts/calculated/interpretation legend, limitations, follow-up, new conversation, failure keeps input
- ✅ Evidence: desktop drawer, focus return, figures & calculations with primary filing page
- ✅ Promise Ledger: commitments / aspirations / other, plain explanations, transcript coverage in empty state
- ✅ Alerts: review preview with AND/OR, unsupported parts before saving, evaluation status, edit threshold
- ✅ Five journeys verified at 390 px; desktop Briefing / Research / Ledger at 1440 px (screenshots in docs/screenshots)
- ⬜ Screen-reader pass, measured contrast audit, real-device safe areas, Playwright E2E

## Financial correctness (15 Nov 2026) — see docs/financial-correctness.md
- ✅ Capital events: instrument / action / direction / stage / amount / evidence; repayment ≠ raise; approval ≠ completion
- ✅ Claim-level references (F#/C#) with formula, inputs, basis, period and primary page; grouped citation controls
- ✅ Validator rejects wrong sign, unit, period, basis, company, metric; unsupported → removed or insufficient evidence
- ✅ Causal clauses require a cited passage mentioning the cause
- ⬜ Stronger causal entailment check; derived sequential changes as registered calculations

## Reliability & evaluation (15 Nov 2026) — see docs/reliability-and-evaluation.md
- ✅ Durable ingestion jobs with leases, bounded retry, terminal errors, stage progress, restart recovery (live-verified)
- ✅ Versioned financial snapshots/observations; validate before promote; restatements preserved
- ✅ Honest window coverage (gaps, never-checked, failing); session-aware event timing
- ✅ Workspace key on mutations, server-side proxy, strict CORS, production config checks
- ✅ Held-out evals: retrieval (n=24), research (n=14 ×2 runs), materiality (n=28) with usage/cost
- ⬜ Retrieval recall (46% top-5) — needs a separate dev set; hybrid needs corpus embeddings

## Handover · Competing Hypotheses · Replay (15 Nov 2026) — see docs/handover-hypotheses-replay.md
- [x] M0: evidence contracts; NCD/CP/QIP allotments no longer read as repayments; AND semantics; 20-session volume rule; repeats don't reopen reviews
- [x] M1 Investor Handover: plan composer → review → save; versions, pause/resume; return briefing (new / meaningful updates / resolved / open / unable / repeats); explicit versioned review; persistence after restart
- [x] M2 Competing Hypotheses: cash-conversion investigations; conflicting (LT), insufficient (SUNPHARMA), incompatible (quarterly; lender); versions; hand-off to the briefing
- [x] M3 Historical Alert Replay: coverage check; point-in-time evaluation with the production evaluators; cancel / resume; known match, non-match, missing data, insufficient history, later revision
- [x] 152 backend tests; tsc, lint, build; browser 390 px and 1440 px
- [ ] Replay screenshot (verified via DOM only); screen-reader pass; broader hypothesis question families; half-year cash flows

## Next up
1. Deploy (Supabase → Render → Vercel) and run the first ingestion.
2. Add an LLM key; run `evals/scripts/run_evals.py` in LLM mode and record cost/latency.
3. Playwright test for Briefing → Investigate → Evidence → Alert at 375 px.
