# Architecture

## Components

| Layer | Module | Responsibility |
|---|---|---|
| Frontend | `frontend/app/*` | Mobile-first PWA: Briefing, Portfolio, Research (Ask / Numbers / Promises / Sources), Alerts & settings, global Evidence Lens sheet |
| API | `backend/app/api/routes.py` | Versioned REST (`/api/v1`), Pydantic request validation, rate limits on expensive routes, admin-token guard on ingestion |
| Push workflow | `backend/app/agents/filing_agent.py` | Filing → category → facts → score → card, with citations |
| Pull workflow | `backend/app/agents/research_agent.py` | Question → tools → cited answer (LLM tool loop or extractive) |
| Promise Tracker | `services/guidance.py`, `services/promise.py` | Extraction, revision detection, comparison |
| Deterministic services | `services/finance.py`, `periods.py`, `market.py`, `metrics.py`, `scoring.py`, `alerts.py`, `classifier.py` | All arithmetic, ranking, rule evaluation |
| RAG | `rag/pdf.py`, `rag/chunker.py`, `rag/retriever.py` | Page-aware extraction, speaker-aware chunking, hybrid retrieval |
| Ingestion | `ingestion/providers.py`, `screener_parser.py`, `http.py`, `pipeline.py` | Replaceable adapters, polite fetching, idempotent upserts |
| Durable jobs | `services/jobs.py`, `workers/job_worker.py` | Document ingestion as leased jobs: status, attempts, backoff, error class, stage progress, restart recovery |
| Financial history | `services/financial_history.py` | Source snapshots + append-only observations; validate before promoting; `financial_metrics` is the projection |
| Coverage & timing | `pipeline.record_listing_coverage`, `routes._window_coverage`, `market.event_timing` | What a limited source actually covered; filing time vs trading sessions |
| LLM | `llm/client.py`, `llm/prompts.py` | Provider-agnostic client, call caps, token/cost tracking, versioned prompts |
| Evidence contracts | `domain/evidence.py` | `FinancialFact`, `CalculatedResult`, `DocumentClaim` (verbatim passage + document version), item kinds |
| Investor Handover | `services/monitoring.py`, `api/plans.py` | Typed monitoring plans, versioned; deterministic evaluation → return briefing; shared `market_check` / `filing_matches` rule evaluators |
| Competing Hypotheses | `services/hypotheses.py`, `api/investigations.py` | Cash-conversion investigations: computed evidence + quote-checked passage classification; versioned evidence |
| Alert Replay | `services/replay.py`, `api/plans.py` | Point-in-time replay of a plan version with the production evaluators; durable, cancellable, resumable runs |

## Daily refresh

`workers/scheduler.py` runs `pipeline.run_daily` in-process at 08:30 IST (overnight filings) and 18:30 IST (after
market close), plus a catch-up run ~60 s after startup when the last successful run is older than 12 h. One run at a
time (lock); each run is an `ingestion_runs` row. Steps: EOD prices → filing index per company → filing analysis →
**document refresh** (full text of the last 7 days' non-routine filings, any newly published call transcript (latest 2
per company) and the latest annual report; already-indexed or failed documents are skipped; ≤60 new documents per run)
→ alert evaluation. Newly indexed transcripts update the Promise Ledger automatically. "Refresh now" in Alerts (and
`POST /api/v1/admin/ingest/daily`) triggers the same run. On hosts that sleep (Render free tier) the GitHub Actions cron
calls the same endpoint; set `SCHEDULER_ENABLED=false` there to avoid double runs.

## Durable document ingestion

Every document download/extract/chunk/analyse runs as an `ingestion_jobs` row keyed `doc:<id>`, whoever asks for it
(a research question, the daily refresh, a filing opened from the Briefing, or a background worker):

```
ensure_job(key) ──▶ claim (one conditional UPDATE: queued/retry & due, or running & lease expired)
                     │ only one caller gets rowcount = 1 — threads or separate processes, SQLite or Postgres
                     ▼
     download ──▶ extract ──▶ chunk(+embed) ──▶ analyse (transcripts → Promise Ledger)
     each stage = heartbeat: progress saved, lease renewed (120 s); download persisted before extraction
                     ▼
  succeed (result: pages, chunks, guidance items, download fetched|reused)
  or fail → classify: network / rate_limited / server_error / unknown → retry at 30 s, 60 s, 120 s … ≤ 30 min, max 4 attempts
                      not_found / access_denied (401/403) / forbidden host / not_pdf / too_large / no_text → terminal, explained
```

* **Reuse:** finished jobs are never repeated; a job running elsewhere is left alone; a job in backoff is not retried
  early; a retry after a failed extraction reuses the stored PDF.
* **Recovery:** a worker that dies stops renewing its lease; once it expires any worker (the API's in-process thread,
  `python -m app.cli worker`, or a request) may claim the job, and `attempts` keeps counting. On startup the worker also
  marks expired `running` jobs as retryable (`error_class = interrupted`).
* **Coverage report** (`GET /api/v1/companies/{t}/coverage`): listed, downloaded, extracted, indexed and analysed counts
  per document type, terminal failures with explanations, pending retries with their next attempt time, job totals.

Verified on the live stack (15 Nov 2026): three workers (two CLI processes + the API thread) shared five queued
transcripts — each ran exactly once (attempts = 1); a worker killed with SIGKILL mid-extraction left its job "running";
after lease expiry the API's worker finished it on attempt 2, reusing the downloaded PDF. Tests:
`tests/test_jobs.py` (8 threads and 4 spawned processes race for one job; exactly one wins).

## Financial data history

Each fetch of standardised results becomes a `financial_snapshots` row (source, URL, source hash, extraction version,
basis, observed/last-seen times). An identical hash only moves `last_seen_at`. Otherwise the candidate is validated
against current values — statement basis unchanged, known units, finite values, no negative revenue/borrowings,
recognised periods (Q/H/FY), quarterly history not shrinking by half, revenue/PAT present, and not a mass change
(>40% of overlapping values moving >20%, which signals a unit or basis error). Rejected snapshots are kept with their
reasons and change nothing. Promoted snapshots add `financial_observations` only for new or changed values; a changed
value supersedes (never overwrites) the old one, so restatements stay queryable
(`GET /api/v1/companies/{t}/financials/history`). Periods that roll out of the source's window remain current.
`financial_metrics`, which the app reads, is updated row by row from current observations.

## Coverage and event timing

* The filings listing returns only each company's latest filings. `source_coverage` records, per company, the last
  successful listing's oldest/newest items and `continuous_since` (reset when a new listing's oldest item is newer than
  the previous newest, i.e. filings may have been missed in between). The Briefing reports, per window, whether every
  holding is covered, from when coverage is complete, which companies have gaps, which were never checked and which
  last check failed. A 30-day window is honestly partial: the source cannot cover it.
* `market.event_timing` places a filing against the 09:15–15:30 IST session: `pre_event` (last close before
  publication — context), `mixed_session` (filed during trading: that day's bar straddles the filing) and `reaction`
  (first session entirely after publication). Cards, scoring and research use only `reaction` as "after the filing";
  research marks filings made after a move's close as unable to explain it. Timing is never presented as cause.

## Public deployment boundary

FINN is a **single shared workspace**: one portfolio, one alert list, one set of preferences. Anyone who can write
changes them for everyone.

* Browsers call the web app's own `/api/finn/*` route (same origin). It forwards to `FINN_API_URL` and adds
  `X-Workspace-Key` from the server's `FINN_WORKSPACE_KEY` for writes; the key never reaches the browser (no
  `NEXT_PUBLIC_*`). Admin routes are not proxied.
* The API requires `X-Workspace-Key` (constant-time compare) on every mutating `/api/v1` request — including research,
  which can trigger downloads and model spend — and `X-Admin-Token` on `/api/v1/admin/*`. With no key configured,
  writes are accepted only from localhost (development).
* `APP_ENV=production` refuses to start without 24+ character admin and workspace tokens, with `*` or localhost in
  `CORS_ORIGINS`; API docs are disabled. CORS lists explicit methods and headers, no credentials.
* Optional `FINN_SITE_PASSWORD` puts HTTP basic auth in front of every page for a public demo. Rate limits are
  in-memory per instance and, behind the proxy, per proxy address.

## Evaluation

`evals/scripts/heldout_eval.py` measures retrieval (recall@5/10, MRR on 24 held-out questions, 17 companies, 2
unfamiliar), end-to-end research (citation coverage, numerical attribution, lexical claim-support proxy, gold passage
cited, abstention, latency, actual tokens/cost from `agent_runs`) and materiality (28 sampled real filings). Results:
`evals/reports/heldout.md`; decisions and failure examples: `docs/reliability-and-evaluation.md`.

## Push data flow

```
cron (GitHub Actions 16:30 IST) ─▶ POST /admin/ingest/daily
  1. refresh_market       yfinance EOD bars → prices (upsert by company+date)
  2. upsert_company       document-index page → filed-results metrics (replace), documents (by URL),
                          announcements (by sha256(url)) + event_group
  3. process_pending      for each new filing:
       classify (rules)  ─▶ conf < 0.7 and LLM? ─▶ fast-model classification (quote-verified)
       content category?  ─▶ download filing PDF (allow-listed host, size cap) → chunks
       facts: results lines from structured table · person/role/profile · rating/agency/action · ₹ amounts
       score = baseline + modifiers + size-vs-revenue + novelty + market reaction + preference (+ LLM ±10)
       guardrail: protected categories never fall below "medium"
  4. evaluate_all         alert rules over latest snapshot and last-7-day events (unique dedupe_key)
```

The Briefing groups filings by `event_group` (same company + category + IST day for results/calls; normalised
title otherwise), shows the highest-scored member and lists the rest as related filings.

## Pull data flow

```
POST /research {ticker, question, filing_id?}
  company unknown → resolve from document index (any NSE symbol) → refresh prices
  ensure_research_corpus: latest 4 transcripts + latest annual report (download → pages → chunks → guidance)
  filing_id → index that filing and its same-event siblings, search them first
  tools: get_financial_snapshot · get_annual_overview · search_documents · get_promise_tracker ·
         calculate_growth · calculate_margin
  LLM mode: reasoning-tier tool loop (≤6 steps), answer must cite [E#]; unknown ids are stripped + flagged
  extractive mode: intent routing → numbers [M] + verbatim management quotes [E#]
```

**Numbers vs narrative.** Numerical questions are answered from `financial_metrics` through deterministic
calculators. Narrative questions use retrieved chunks. Table-heavy chunks are down-weighted for narrative
queries so numbers are never parsed out of table fragments.

## Research chat and Alerts assistant

```
POST /research {ticker, question, filing_id?, history[]}
  reasoning-tier tool loop (≤6 steps): get_recent_filings · read_filing · get_price_moves · get_financial_snapshot ·
  get_annual_overview · search_documents · get_promise_tracker · calculate_*
  → grounding checks: unknown [E#] stripped · figures not present in any tool output listed as "verify" caveat
  → fallback: extractive composer (intent routing: recent_filings, price_move, guidance, …)

POST /assistant/alerts {message, history[]}
  fast-tier tool loop (≤8 steps): list_rules · list_recent_alerts · get_preferences · list_companies ·
  get_market_snapshot · propose_create/update/delete_rule · propose_set_thresholds · propose_set_watched_categories
  → proposals validated immediately (errors fed back to the model) → returned as typed actions
POST /assistant/alerts/apply {actions[]}  → executed only on user confirmation
  fallback: rule-based planner that splits compound requests into clauses
```

Shared runtime: `agents/runtime.py` (loop, step cap, tool allow-list, captured outputs) and
`agents/grounding.py` (citation + number checks). See ADR-008.

## Agent design

Two workflows, not a swarm (ADR-004). The Filing Intelligence workflow is mostly deterministic and calls a
cheap model only when the rules are unsure or a material filing needs a summary. The Research agent is the
only place a reasoning-tier model runs, and only once per user question. Guidance extraction is a workflow
(rules-v1, optional LLM extractor) rather than a third agent.

Prompt-injection posture: document text is wrapped as untrusted data, never concatenated into instructions;
tools are read-only; LLM quotes must exist verbatim in the source or the output is flagged for review; the
extractive path cannot be steered at all because it only quotes.

## Investor Handover, Competing Hypotheses and Alert Replay

See `docs/handover-hypotheses-replay.md` for workflows, results and limitations; ADR-013 for the design decisions.

```
plan text ──parse_plan──▶ PlanSpec (validated) ──save──▶ monitoring_plans (+ versions)
                                                           │
daily refresh ─▶ hypotheses.refresh_all (no model calls) ──┤──▶ monitoring.evaluate ─▶ plan_evaluations (idempotent)
                                                           │          │
Briefing ◀── GET /plans/{id}/briefing (read-only) ◀────────┘          ├─ filing_matches (event groups, Event DNA roles)
   └─ Mark reviewed → event_reviews (exact filing ids shown)          ├─ guidance_cuts (same measure/period/basis)
                                                                      ├─ market_check (EOD; AND/OR; prev-20 volume)
Research ▸ For & against ─▶ hypotheses.run ─▶ investigations (+ versions) ─▶ "evidence changed" in the briefing
Alerts ▸ Test on past data ─▶ replay_runs ─▶ replay.execute(thread): per session, same evaluators with as-of data
```

* **One rule implementation.** Live monitoring and replay call the same `market_check`, `filing_matches` and
  `guidance_cuts`; replay passes point-in-time inputs (`snapshot(as_of=…)`, filings published by the session's end,
  guidance dated on or before it) instead of re-implementing rules.
* **Stable ids and explicit review.** Briefing items carry `review_key` + `version_ids` (the filings shown). Review writes
  only those ids; viewing writes nothing.
* **Bounded model use.** Plans are parsed by rules. Competing Hypotheses makes at most one model call per comparison
  (≤ 8 passages), every judgement quote-checked; scheduled re-checks make none. Prompt `HYPOTHESES_PROMPT_VERSION`.
* **Durability.** Plan evaluations dedupe on (plan, version, fingerprint). Investigations store a new version only when
  the evidence fingerprint changes. Replays save after every session, honour cancel, and become *interrupted* (resumable)
  after a restart; a claim is a conditional UPDATE so two workers cannot run the same replay.

## Database

Tables (see `backend/app/models/__init__.py`, migration `backend/alembic/versions/*_initial_schema.py`):
`companies`, `portfolios`, `portfolio_holdings`, `prices`, `announcements`, `event_analysis`, `documents`,
`document_chunks`, `financial_metrics`, `management_guidance`, `alert_rules`, `alerts`, `ingestion_runs`,
`agent_runs`, `user_preferences`, `ingestion_jobs`, `financial_snapshots`, `financial_observations`, `source_coverage`
(migration `*_jobs_observations_coverage.py`), `event_reviews`, `monitoring_plans`, `monitoring_plan_versions`,
`plan_evaluations`, `investigations`, `investigation_versions`, `replay_runs` (migrations `9c4d…`, `a1d5…`, `b2e6…`, `c3f7…`).

Key constraints: `prices(company_id, trading_date)` unique; `announcements(company_id, content_hash)` unique;
`documents(company_id, source_url)` unique; `financial_metrics(company, period, period_type, basis, metric)`
unique (the current-value projection); `ingestion_jobs.key` unique (one job per unit of work);
`source_coverage(company_id, source)` unique; `alerts.dedupe_key` unique (idempotent alerting);
`plan_evaluations(plan_id, plan_version, fingerprint)`, `monitoring_plan_versions(plan_id, version)`,
`investigation_versions(investigation_id, version)` and `event_reviews.event_key` unique.

## Retrieval

BM25 over the company's chunks with light financial synonym expansion, fused (RRF) with cosine similarity when
embeddings exist. Contextual chunk scoring and proximity reranking are implemented as options but **not enabled**: on
the held-out set they did not improve recall@5 (all 11/24); reranking raised MRR (0.254 → 0.327) but lost one top-10
hit. Hybrid retrieval could not be evaluated (1% of chunks have embeddings, provider quota). Filters: company, document type/ids, speaker role, reporting period. Management turns get a
small boost, moderator boilerplate a penalty. At prototype scale (≈1–2k chunks per company) in-process scoring
takes milliseconds; pgvector is the documented upgrade (ADR-003).

## Trade-offs

* **Screener as document index** instead of NSE/BSE APIs: reliable, permitted by robots.txt for company pages,
  links straight to exchange PDFs — but limited to the latest ~5 announcements per company.
* **Deterministic-first AI**: works with zero spend and is fully testable; LLM features are additive.
* **SQLite locally, Postgres in prod** via one SQLAlchemy model set; embeddings stored as JSON for portability.
