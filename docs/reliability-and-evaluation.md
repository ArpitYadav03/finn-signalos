# Ingestion reliability, financial history and held-out evaluation

Date: 15 Nov 2026. Architecture details: [architecture.md](architecture.md) (sections "Durable document ingestion" to
"Evaluation"). Raw results: `evals/reports/heldout.md` / `heldout.json` (run 2) and `heldout_run1.*` (run 1).

## What was built

| Area | Implementation |
|---|---|
| Durable ingestion | `ingestion_jobs` with status, attempts, next retry, error class + explanation, stage progress, lease; every `index_document` caller goes through it; in-process worker thread + `python -m app.cli worker` |
| Coordination & recovery | Lease claimed by one conditional UPDATE; heartbeat per stage; expired leases are reclaimable; startup recovery |
| Retries | Transient (network, 429, 5xx, unknown): 30 s → 60 s → 120 s … capped at 30 min, max 4 attempts. Terminal (404, 401/403, off allow-list, not a PDF, too large, no text): kept with explanation |
| Coverage by stage | `GET /companies/{t}/coverage`: listed / downloaded / extracted / indexed / analysed per document type, failures, pending retries |
| Financial history | Snapshots (hash, extraction version, timestamps, validation) + append-only observations (restatements supersede, never overwrite); validated before promotion; `financial_metrics` updated row by row |
| Honest coverage | Per-company listing boundaries and `continuous_since`; Briefing shows whether the window is covered, gaps, never-checked and failing sources |
| Event timing | `event_timing`: pre-event close, mixed session (filed during trading), first full post-event session; used by cards, scoring and research; never causal |
| Deployment boundary | Workspace key on all `/api/v1` mutations; admin token; same-origin Next.js proxy keeps the key server-side; strict CORS; production refuses unsafe config; optional site password; single-workspace scope documented |
| Evaluation | Held-out retrieval (24 questions, 17 companies, 15 sectors, 2 unfamiliar tickers), end-to-end research (14 cases incl. 4 unanswerable), materiality (28 sampled filings) |

## Verification

* **Tests: 133 passed** (24 new: `test_jobs.py` 16, `test_financial_history.py` 3, `test_timing_coverage.py` 2,
  `test_deployment_boundary.py` 2, plus a validator regression). Frontend `tsc`, `next lint`, `next build` clean;
  Alembic upgrade → downgrade → upgrade clean on a fresh database.
* **Duplicate-job prevention:** 8 threads and 4 separately spawned processes race to claim one job — exactly one wins
  (tests). Live: two CLI worker processes and the API's worker shared 5 queued UltraTech transcripts; each was
  processed once (attempts = 1): worker 1 took 2, worker 2 took 2, the API worker took 1.
* **Restart recovery (live):** a worker was SIGKILLed while extracting UltraTech's 292-page FY25 annual report; the job
  stayed "running" with a 119 s lease; after expiry the API worker completed it on attempt 2 with `download: reused`.
* **Partial-source failure (live):** listings for 18 holdings refreshed with Polycab's fetch forced to time out. The
  Briefing reported the 24 h window as not complete, naming Polycab as failing with its last successful check; the
  other 17 refreshed normally. A real failure also appeared: Apollo Hospitals' website answers 403 to automated
  downloads — first classified "unknown" (would retry 4×), now terminal "access_denied" with an explanation; FINN does
  not work around it.
* **Financial history (live):** the first real refresh rejected all 23 snapshots — the source emits half-year columns
  (H1FY27) that the validator didn't recognise. Served data stayed untouched (the intended failure mode); fixed with a
  regression test. After the fix: 18 snapshots promoted, 9,707 observations; served metrics equal current observations
  (checked for TCS, HDFC Bank, Polycab); an identical second refresh created no new snapshots.
* **Unfamiliar-company journey (UI):** Coal India (never ingested) via Research → found in the index → 10 durable jobs
  (4 transcripts, annual report, 5 filings), all succeeded → business description quoted from the annual report →
  cited answer on production and offtake with an explicit limit (no FY27 volume guidance disclosed).

## Retrieval benchmark (no model calls, n = 24)

| Variant | Recall@5 | Recall@10 | MRR@10 |
|---|---|---|---|
| Keyword BM25 (current) | 45.8% (11/24) | 58.3% (14/24) | 0.254 |
| Contextual chunks | 45.8% (11/24) | 58.3% (14/24) | 0.255 |
| Keyword + proximity rerank | 45.8% (11/24) | 54.2% (13/24) | 0.327 |
| Contextual + rerank | 45.8% (11/24) | 54.2% (13/24) | 0.327 |

Decision rule, set before running: adopt only with ≥2 more gold passages in the top 5 and no MRR drop. **No variant
qualified; keyword BM25 stays.** Hybrid retrieval was not evaluated: only 335 of 33,148 chunks (1.0%) have embeddings
because of the embedding provider's free-tier quota. Typical misses: call openings and moderator text outrank the
answer ("What dividend did the board recommend?" → "I'll let the Board decide…"), and questions phrased differently
from the passage (ARPU, NIM, AUM growth). Retrieval is the weakest link measured here.

## End-to-end research (n = 14: 10 answerable, 4 unanswerable; model via fallback chain)

| Metric | Run 1 | Run 2 (after a validator fix) |
|---|---|---|
| Citation coverage (sentences with a reference) | 92.6% (50/54) | 82.0% (41/50) |
| Numerical attribution (final-answer figures with a reference) | 100% (58/58) | 100% (47/47) |
| Document-claim support, lexical proxy | 82.5% (33/40) | 75.0% (24/32) |
| Gold passage retrieved and cited | 50.0% (4/8) | 50.0% (4/8) |
| Correct abstention | 75.0% (3/4) | 75.0% (3/4) |
| False abstention | 10.0% (1/10) | 20.0% (2/10) |
| Validator: figures checked / statements removed | 64 / 5 | 53 / 4 |
| Latency p50 / p90 / max | 5.9 / 10.9 / 36.5 s | 4.5 / 11.2 / 18.1 s |
| Usage (actual, from agent_runs) | 45 calls, 150,311 in + 3,816 out tokens, est. $0.055 | 45 calls, 170,438 in + 3,498 out, est. $0.060 |

Between runs only the validator changed (bullet labels now name the metric — run 1 removed correct "**Revenue:**
Reported at ₹…" figures). The other differences are model variance: the provider fallback served different models
(`gemini-3.5-flash-lite` / `gemini-3.1-flash-lite`). With n = 14, differences of one or two cases are not meaningful.

Failure examples:
* **Wrong figure that passes the validator (Bharti, run 2):** "ARPU for the quarter was ₹264" — the cited passage says
  "came in at Rs.271, up from 264". The number is in the passage, so the presence check accepts it; it is the previous
  quarter's value. Passage-number matching is not semantic attribution.
* **Retrieval-driven false abstention (ICICI, run 2; Bharti, run 1):** the NIM/ARPU passage was not retrieved, so the
  answer said the figure isn't disclosed.
* **Tool not used (UltraTech, run 2):** "latest quarter vs a year ago" answered as "not disclosed" without calling the
  results tool.
* **Strict abstention scoring (Pidilite FY35):** the answer correctly said FY35 isn't reported but added FY26 figures;
  counted as not abstaining.
* **Unregistered figures removed:** fuel cost "₹2.09 per kcal" (UltraTech) and "₹252 billion" (Polycab) weren't
  found in the cited passages and were removed — correct behaviour if the passages lack them; not verified by hand.

## Materiality (n = 28 random real filings, developer-labelled blind to FINN's scores)

Accuracy 82.1% (23/28); missed material 1/5 (an L&T order win scored routine — the order size isn't disclosed);
false alarms 4/23 (Bajaj half-year report high, ITC subsidiary merger high, Reliance rating reaffirmation medium, HDFC
Bank business-head superannuation medium).

## Remaining limitations

* Evaluation labels are the developer's, sample sizes are small, and claim support is a lexical proxy.
* Retrieval recall is low (46% in the top 5); the measured variants didn't fix it, and no tuning was done on the held-out
  set. Next step: a separate development set for retrieval work (e.g. down-weighting call openings), then re-test here.
* Hybrid retrieval needs embeddings for the corpus (paid quota or a local embedding model) before it can be measured.
* Leases assume roughly synchronised clocks; the in-memory rate limiter is per instance and sees the proxy's address.
* Coverage boundaries start from when tracking began (15 Nov 2026); earlier gaps are reported as "covered from" the first
  listing, not reconstructed.
* Session timing uses each company's stored bars as the trading calendar; exchange holidays without stored bars after the
  latest bar are assumed to be sessions until a bar shows otherwise.
* Not deployed; the deployment boundary is verified by tests and configuration checks, not on a public host.
