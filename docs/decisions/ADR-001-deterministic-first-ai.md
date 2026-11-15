# ADR-001: Deterministic first, LLM where it adds judgment

**Status:** accepted · **Date:** 2026-11-15

**Context.** The brief rewards efficient model use and forbids unsupported numbers. No LLM key was available during the build.

**Decision.** Every number, ranking rule, alert evaluation, period match and dedupe is plain Python. LLMs are used for (a) classifying filings the rules are unsure about, (b) polishing summaries of material filings, (c) research synthesis, (d) optional guidance extraction. Each LLM step has a deterministic fallback, and the UI states which mode produced an answer.

**Consequences.** The app works with zero spend and is fully unit-testable. Research answers in extractive mode are less fluent (verbatim quotes + computed facts) but cannot hallucinate. Adding a key upgrades quality without code changes.
