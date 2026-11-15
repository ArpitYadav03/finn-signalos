# ADR-011: Deterministic guards on agent proposals

Status: accepted · 15 Nov 2026

## Context
The alerts assistant turns plain language into typed `AlertRuleSpec` proposals that the user reviews before saving.
During live verification the model (gemini-flash-lite) once answered "Alert me on any CEO or CFO change" with two
conditions joined by OR: *any leadership change* and *a leadership change involving the CEO or CFO*. The first
condition subsumes the second, so the saved rule would have fired on every director change — the opposite of what
the user asked for. A second run of the same request returned the correct single condition: the failure is
intermittent, which makes it easy to miss in review.

## Decision
Every proposal passes through `alerts.tighten(spec, user_text)` before it is shown:
* roles named in the user's words (CEO, CFO, MD, chairperson, COO) are applied to an unfiltered leadership condition;
* "downgrade" / "upgrade" is applied to an unfiltered credit-rating condition;
* an unfiltered filing condition that duplicates a filtered one is dropped (or narrowed to its other categories).
The review card always shows the tightened rule, so what the user approves is what gets saved.

## Consequences
* The user's precision survives model variability; the guard is unit-tested.
* The guard only ever narrows a rule toward the user's own words; it never invents conditions.
* Same principle as ADR-001 (deterministic first) and ADR-008 (confirmable agent actions).
