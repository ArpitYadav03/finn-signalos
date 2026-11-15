# FINN SignalOS

**Signal first. Evidence always.** A mobile-first investment-intelligence copilot built for the ENAM Holdings
*FINN: Financial Investment Agent* challenge.

FINN watches an 18-stock NSE portfolio and does two things:

* **Push — Briefing.** Every exchange filing across the portfolio is classified, scored for materiality with
  a visible rationale, grouped into events, and either surfaced as a card ("why it matters", headline numbers,
  source) or collapsed as routine. End-of-day price/volume anomalies and user-defined alerts sit alongside.
* **Pull — Research.** Tap **Investigate** on any card (or type any NSE symbol) and FINN opens the company with
  the filing in context and answers from primary documents only — annual reports, call transcripts and filed
  results — with every claim linked to its page in the **Evidence Lens**.

The differentiator is the **Management Promise Tracker**: guidance is extracted from management (never
analysts) in earnings-call transcripts and checked against reported numbers in Python — with refusals when the
period, basis (constant currency, segment) or metric doesn't line up.

> Research aid, not investment advice. No trade execution, no buy/sell calls.

---

## Submission status (15 Nov 2026)

**READY WITH DISCLOSED LIMITATIONS** (application). The CIPLA, INFY and ASIANPAINT extraction defects are fixed with regression tests. Disclosed limitations: one AI-classified L&T passage of a different period and scope (verdict unchanged), and the indexed-mode replay guidance cutoff. The submission links below are still missing. See [docs/submission-status.md](docs/submission-status.md) for evidence, repair specifications, verification results, the demo script and missing items.

- Deployment URL: **missing** (not deployed; localhost is local only)
- Repository URL: **missing** (not pushed)
- Build-history URL: **missing** (local file `docs/ai-build-history.md`)

## What works today (verified on live data, 15 Nov 2026)

| Area | Status | Notes |
|---|---|---|
| EOD prices + anomalies (18 holdings) | ✅ live | Yahoo Finance via `yfinance`; return vs prior close, volume ÷ 20-session avg |
| Filing ingestion → classification → scoring → cards | ✅ live | BSE filings discovered through the Screener.in document index (latest 5 per company); related filings grouped into one event (ADR-009) |
| Results cards (Revenue / EBITDA / PBT / PAT, QoQ & YoY) | ✅ live | Deterministic from filed results; lenders show financing profit, never EBITDA |
| Management-change cards with profile from the filing | ✅ partial | Name/role/action extracted; profile when the Reg-30 letter includes one. No LinkedIn scraping |
| Research on any NSE ticker | ✅ live | Resolves unseen tickers on demand; indexes 4 transcripts + latest annual report |
| Evidence Lens (page, speaker, excerpt, open PDF at page) | ✅ live | Citations come only from retrieved chunks |
| Promise Ledger | ✅ live, rules-v1 | Completed: Infosys FY26 operating-margin guidance 20–22% → 20.92% (met), CFO-reported 21% cross-checked. Interim: FY27 20–22% vs 21.0% in Q2 |
| Research chat (multi-turn, filing-aware) | ✅ deterministic · ⚙️ AI mode | Follow-ups keep context; "latest news" = the company's own exchange filings; "why did it move" = EOD moves + filings around those dates |
| Alerts assistant (agent) | ✅ deterministic · ⚙️ AI mode | "Pause the Reliance alert and warn me if Maruti drops 5%" → checkable proposed actions → Apply |
| Alerts (validated rules, deterministic evaluation) | ✅ | In-app alerts; dedupe per rule/event/day |
| AI research chat + alerts agent | ✅ live with Gemini (15 Nov 2026) | Research chat on `gemini-3.5-flash` (8–21 s per answer, cited); alerts agent on `gemini-flash-lite-latest` (~3 s). Also tested with a scripted fake LLM in CI |
| LLM filing polish / guidance extraction | ⚙️ wired, not yet evaluated on real data | Runs automatically on new filings once a key is set |
| Investor Handover (monitoring plans + "Changes since your last review") | ✅ | Plain-words plan → reviewed typed plan → saved, versioned, pausable; return briefing with repeats suppressed and source failures shown as "unable to evaluate" — [docs](docs/handover-hypotheses-replay.md) |
| Competing Hypotheses ("Evidence for and against") | ✅ narrow | Cash conversion / working capital: computed evidence with inputs + quote-checked passages; conflicting (LT), insufficient (SUNPHARMA), incompatible (quarterly, lenders); no verdicts |
| Historical Alert Replay ("Test this plan on past data") | ✅ | Production rule evaluators run point-in-time over ~45 stored sessions; coverage check, cancel/resume; no alerts sent, no return claims |
| Deployment | ⚙️ deployment-ready, **not deployed** | Dockerfiles, compose, CI, Render/Vercel/Supabase steps in `docs/deployment.md` |

## Architecture (one screen)

```
              ┌──────────── Next.js PWA (mobile-first) ────────────┐
              │ Briefing · Portfolio · Research · Alerts · Evidence │
              └───────────────────────▲─────────────────────────────┘
                                      │ REST /api/v1 (typed, Pydantic)
┌─────────────────────────────── FastAPI ─────────────────────────────────┐
│ Filing Intelligence workflow        Fundamental Research agent          │
│  rules classifier → (LLM if unsure)  intent → tools → answer + [E#]     │
│  facts · size-aware explainable score  search_documents (BM25+vec)      │
│  results numbers (deterministic)       get_financial_snapshot / promises│
│ Promise Tracker: transcript → guidance → period/basis match → Python    │
│ Alert engine: NL → AlertRuleSpec (validated) → evaluated without LLM    │
├─────────────── source adapters (replaceable) ───────────────────────────┤
│ YahooMarketProvider · ScreenerCompanyProvider (index of BSE/IR PDFs)    │
│ FixtureAnnouncementProvider (import genuine filings offline)            │
└─────── SQLite (local) / PostgreSQL+pgvector (Supabase) · PDF store ─────┘
```

Details: [docs/architecture.md](docs/architecture.md) · decisions: [docs/decisions/](docs/decisions/) · data: [docs/data-sources.md](docs/data-sources.md) · polish & verification: [docs/product-polish.md](docs/product-polish.md) · financial correctness: [docs/financial-correctness.md](docs/financial-correctness.md) · reliability & evaluation: [docs/reliability-and-evaluation.md](docs/reliability-and-evaluation.md) · investor workflow & demo: [docs/investor-workflow.md](docs/investor-workflow.md) · screenshots: [docs/screenshots/](docs/screenshots/)

## Run it locally

Requirements: Python 3.11+, Node 20+ (22 recommended).

```bash
cp .env.example .env            # optional: add LLM_API_KEY for AI synthesis
./scripts/dev-backend.sh        # http://localhost:8000  (creates backend/.venv on first run)
```

```bash
cd backend && .venv/bin/python -m app.cli daily                     # prices + filings + analysis + alerts
cd backend && .venv/bin/python -m app.cli research-corpus TCS INFY  # index transcripts + annual report
```

```bash
./scripts/dev-frontend.sh       # http://localhost:3000
```

Or everything in containers (Postgres + pgvector, backend, frontend): `docker compose up --build`.

## Tests and evaluation

```bash
cd backend && .venv/bin/python -m pytest -q          # 72 tests: finance, periods, classifier, scoring, guidance, alerts, API, agents, dedup, push→pull
cd backend && .venv/bin/python ../evals/scripts/run_evals.py   # writes evals/reports/latest.md
```

Latest measured results are in [evals/reports/latest.md](evals/reports/latest.md) (classification accuracy,
guidance precision/recall, numeric checks, citation validity, quote verbatim-ness, latency, cost).
The eval sets are small and partly overlap with development examples — treat them as regression checks,
not a benchmark.

## Demo script (3 minutes)

1. **Briefing → 7d.** "7 things worth your attention" out of ~27 filings; routine filings collapsed.
2. **ITC results card.** Revenue +3.9% YoY but PAT −18.5%, EBITDA margin −6pp — *why it matters* says profit lagged revenue. Tap *why this rank?* to see the score breakdown.
3. **Investigate.** Research opens on ITC with the filing pinned; the answer quotes the filing itself, then the headline numbers. Tap an **E-chip** → Evidence Lens → *Open PDF at page*.
4. **Research → INFY → Promises.** Operating-margin guidance 20–22% for FY27 → 21.0% in Q2 (*on track, interim*); revenue guidance revised to 1–2% and flagged *constant currency → not comparable to INR*.
5. **Type an unseen ticker** (e.g. `ASIANPAINT`) — FINN resolves it from primary sources on the fly.
6. **Ask follow-ups** in the same thread: "What's the latest from the company?", then "Why did the stock move recently?".
7. **Alerts assistant.** "Pause the Reliance alert and warn me if Maruti drops more than 5%" → two proposed changes → *Apply*.

### Turning on the AI agents

Set these in `.env` (backend) and restart: `LLM_API_KEY`, plus `LLM_BASE_URL` and model names if not using OpenAI.
Research chat uses the reasoning model; the alerts assistant and filing classification use the fast model. Each
answer is checked after generation: citations must match retrieved evidence and every figure must appear in a tool
output, otherwise it is flagged in the answer.

## Known limitations

* Filing feed = the latest ~5 filings per company exposed on each Screener company page (more needs login); NSE's API blocks automated clients. The adapter interface is ready for an official/licensed feed.
* Filing timestamps are the exchange publication time; FINN's processing time is shown separately.
* Structured numbers are Screener's standardisation of filed results (labelled everywhere). Company press releases can use a different revenue definition (e.g. ITC's gross revenue).
* EBIT margin for the Promise Tracker is *derived* (operating profit − depreciation) ÷ revenue and labelled as such.
* Rules-based guidance extraction favours precision; it misses guidance phrased without numbers or with unusual wording. The LLM extractor (quote-verified) is implemented but untested without a key.
* Some IR sites serve transcripts through viewers/CDNs that block automated download; those show as *failed* in Sources.

* Competing Hypotheses covers cash-conversion questions only; Replay is limited by stored history (prices from 8 Sep 2026; filings coverage from late Oct) and uses FINN's current analysis of each filing. See [docs/handover-hypotheses-replay.md](docs/handover-hypotheses-replay.md).

More in [docs/submission-note.md](docs/submission-note.md).
