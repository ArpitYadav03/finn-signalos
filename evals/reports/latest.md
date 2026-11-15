# FINN evaluation report

Generated 2026-11-15 12:59 by `evals/scripts/run_evals.py`. All numbers are measured, not estimated.

## Announcement classification (rules-v1)

24 labelled titles (11 real BSE titles from the portfolio, rest synthetic but typical).

**Accuracy: 100.0%**

## Guidance extraction (rules-v1)

11 cases incl. analyst questions, macro data, stake %, past tense and a prompt-injection line.

**Precision 100.0%, recall 100.0%** (tp=6, fp=0, fn=0)

| Case | Result |
|---|---|
| real (TCS Q2 FY27 call, CFO) | ok |
| real (Infosys Q4 FY26 call) | ok |
| synthetic: analyst question must not count | ok |
| synthetic: two commitments in one sentence | ok |
| synthetic: vague optimism is qualitative | ok |
| real-style: macro data is not company guidance | ok |
| real-style: stake % is not guidance | ok |
| synthetic | ok |
| synthetic: word ranges | ok |
| synthetic: past performance is not guidance | ok |
| adversarial: injection without a forward-looking guidance verb | ok |

## Deterministic numeric checks

**10/10 correct**

| Check | Got | Expected | OK |
|---|---|---|---|
| YoY growth | 12.0 | 12.0 | ✓ |
| QoQ decline | -10.0 | -10.0 | ✓ |
| Loss narrowing | 50.0 | 50.0 | ✓ |
| Zero base refused | None | None | ✓ |
| Sign flip refused | None | None | ✓ |
| EBITDA margin | 25.70776630048642 | 25.7078 | ✓ |
| FCF | 650 | 650.0 | ✓ |
| Daily return | -5.0 | -5.0 | ✓ |
| Volume ratio | 3.0 | 3.0 | ✓ |
| Lakh to crore | 0.016 | 0.016 | ✓ |
