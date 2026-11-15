# ADR-013 — Handover, Competing Hypotheses and Replay share deterministic evaluators

Date: 15 Nov 2026 · Status: accepted

## Context
Three capabilities were requested together: saved monitoring plans with a return briefing, competing explanations with
evidence for and against, and replaying a plan on past data. Each could have been built with its own logic and its own
model prompts. The risks were inconsistent answers between live and replayed rules, unverifiable model judgements,
future data leaking into replays, and lost or duplicated review progress.

## Decision
1. **One rule implementation** (`monitoring.market_check`, `filing_matches`, `guidance_cuts`) used by live monitoring and
   replay; replay supplies point-in-time inputs. Fixing a rule fixes both (the AND bug was found this way).
2. **Plans are typed specs parsed by rules**, not by a model; unsupported asks are listed, and at most one clarification
   is asked.
3. **Review is explicit and versioned**: items carry the ids of the filings shown; viewing writes nothing.
4. **Competing Hypotheses is narrow** (cash conversion / working capital from annual statements). Structured evidence is
   computed in `finance.py`; the model only classifies passages, with a verbatim quote that must be found, never on table
   rows; output is a list of evidence and a state (conflicting / one-sided / insufficient / premise not supported), never a
   probability or verdict. Incompatible requests (quarterly cash conversion, lenders) are refused.
5. **Replays are durable rows**, saved per session, cancellable and resumable, executed in-process; they never create
   alerts or modify plans.

## Consequences
* Consistent behaviour live vs replay; regressions in one surface are caught by tests of the other.
* Narrow question coverage for Competing Hypotheses: broader questions need new computed evidence first.
* Replay fidelity is bounded by stored history (≈45 sessions of prices, recent filings coverage, current analysis and
  latest document versions) — disclosed in every run's coverage and limitations.
* In-process replay threads assume a single API process; a multi-instance deployment should move them to the job worker.
