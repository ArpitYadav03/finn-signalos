# ADR-012: Claim-level evidence for numbers and causes

Status: accepted · 15 Nov 2026

## Context
Research answers cited all structured figures with one generic `[M]` marker, and a validator accepted any number that
appeared anywhere in tool output, ignoring sign, unit, period, basis and company. A cash-conversion claim opened
quarterly revenue figures; "PAT fell 6.6%" would pass when PAT rose 6.6%; causal clauses rode on data citations.

## Decision
* Tools register facts (F#) and calculations (C#, with formula and input ids) as they return data; answers cite ids.
* A deterministic validator checks each figure against the cited claim, then against a claim whose metric is named
  beside it, on value, sign, unit, period, basis and company. Document figures must be in the cited passage.
* Unsupported sentences are removed and disclosed; if nothing supported remains, the answer becomes an
  insufficient-evidence response. Causal clauses need a cited passage that mentions the cause.
* The UI opens exactly the cited claims and their inputs, with primary-document pages where found.

## Consequences
* Correct-but-unusually-phrased figures can be removed (safe failure); the rule set is unit-tested.
* The checks verify correspondence, not interpretation; documentation says so explicitly.
* Same principles as ADR-001 (deterministic first) and ADR-011 (deterministic guards on model output).
