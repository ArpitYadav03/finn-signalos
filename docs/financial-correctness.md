# Financial correctness and claim-level evidence

Date: 15 Nov 2026. Scope: the four confirmed review findings, the fixes, how they were verified, and what remains.

## Findings reproduced before changing code

| Finding | Reproduction | Root cause |
|---|---|---|
| Titan commercial-paper redemption described as a capital raise | Card said "Raise of ₹1,000 cr … Check the price, dilution and use of proceeds"; role "Advance notice"; medium priority | The "Fund raise" template had no notion of direction or stage. Also found: the ₹850 cr (9 Nov) and ₹1,000 cr (12 Nov) redemptions were **merged into one event**, because the ingest group id used the title without the date |
| ASIANPAINT annual cash-flow citation opened quarterly revenue/profit | "CFO/PAT FY24 1.10×, FY25 1.19×, FY26 1.61× [M]" opened the Q2 FY27 results table | Every structured figure used one generic `[M]` citation that the UI mapped to the latest quarter |
| Validator accepted sign reversals and unit swaps | `grounding.unverified_numbers` compared absolute values against every number in all tool output | No metric, sign, unit, period, basis or entity semantics |
| Causal explanations accepted generic data citations | "margin contracted … reflecting cost pressures [data]" passed | Causal language was never checked |

## What changed

1. **Capital-markets events** (`backend/app/services/financial_events.py`). Each fund-raise filing is parsed into
   instrument (CP, NCD, bonds, warrants, QIP, rights, preferential, equity), family (debt/equity), action
   (issuance, redemption, repayment, buyback), capital direction (inflow/outflow), stage (proposed → board/committee
   approval → shareholder approval → regulatory approval → issue opened → allotment → completed), amount in ₹ crore,
   per-unit price, ISINs, dates, and the verbatim filing words behind each field. The challenge category stays
   "Fund raise"; the subtype ("Debt repayment — commercial paper redeemed", "Equity raise — warrants") is shown
   beside it. Rules worth noting: "subject to approval of the shareholders" and "to be allotted" are pending, not
   obtained; face value is never the issue price; the filing body is quoted before any index summary.
   Explanations follow the money: repayments say "no new capital is raised and there is no dilution"; only equity
   issuance mentions dilution. Same-transaction identity uses ISIN, then issue price, then amount, so approval →
   allotment of one warrant issue groups and two different CP redemptions do not. Routine debt repayment is scored
   down and is exempt from the protected-category floor.
2. **Claim-level references** (`backend/app/agents/claims.py`). Research tools register every structured figure as a
   fact `F#` (company, metric, value, sign, unit, period, period type, basis, source, primary-document page when
   found) and every derived figure as a calculation `C#` (formula, worked expression, input ids). Annual figures are
   matched to the annual-report line that prints them (e.g. ASIANPAINT FY26 CFO → AR 2026 p.236 "Net Cash generated
   from Operating activities 7,088.18"); quarterly figures to the results filing page. Prompt `research-v6` asks the
   model to cite these ids; the no-AI composer writes them itself.
3. **Citation controls.** Consecutive markers form one group: document passages stay individual chips, figures
   collapse into one control ("FY26 Cash conversion", or "3 figures") that opens exactly those claims plus their
   inputs, with formula, basis and primary/secondary sources. Answers also list "Figures used" as one table.
4. **Validation** (`claims.verify`, run on AI and no-AI answers). Each number must match the claim it cites — or, if
   none matches, a claim whose metric is named next to it — on value (to stated rounding), sign (explicit or words
   like "fell"), unit (%, pp, ₹ crore incl. lakh/billion conversion, ×), period (including "a year ago",
   "sequentially"), statement basis and company. Document numbers must appear in the cited passage with a compatible
   unit. Failing sentences are removed and listed under Limitations; if nothing supported remains, the answer is
   replaced with an insufficient-evidence response.
5. **Causal claims.** "because / due to / driven by / reflecting / led by …" survives only when a cited `[E#]`
   passage shares content words with the stated cause; otherwise the clause is stripped and the observation stays.
   Price-move answers describe filings on the same day as observed timing, not cause.
6. **Formatting.** One formatter (`claims.fmt`): ₹ with Indian grouping and "crore", true minus, sign on changes,
   "pp" for margin changes, "×" for ratios.

## Verification

* Backend: **109 tests pass** (`pytest -q`), including 18 new in `tests/test_claims_events.py`: CP redemption is a
  completed repayment with no raise/dilution language; different CP redemptions are different events; board approval
  ("subject to shareholder approval") is not completion and allotment is not completion; buyback is money out;
  cash conversion keeps its annual CFO/PAT inputs and excludes quarterly figures; correct claims pass; sign reversals,
  unit swaps (lakh, %, ×, pp), wrong period, basis, company and metric are rejected; a number that exists elsewhere is
  not enough; grouped and mismatched references; relative periods; document figures must be in the cited passage;
  unsupported answers become insufficient-evidence; causes without (or with irrelevant) passages are removed and
  supported causes kept; historical performance is not guidance; analyst statements are not management guidance.
* Frontend: `tsc`, `next lint` and `next build` clean.
* Live data: re-analysed Titan and Bajaj Finance. Titan now shows two separate low-priority "Debt repayment —
  commercial paper redeemed · Completed" filings (₹850 cr ISIN INE280A14658; ₹1,000 cr ISIN INE280A14617). Bajaj's
  warrants read Board / committee approval → Allotment (not "Completed"), the QIP reads "Issue opened". Briefing
  (7 days) went from 4 to 3 developments to review.
* Browser (390 px): Briefing → Bajaj card → Details shows the Transaction section with supporting filing quotes
  (`docs/screenshots/m13-transaction-event-dna.jpg`). Research (AI mode) on "Is cash flow keeping up with reported
  profit?" for ASIANPAINT cites each year's CFO, PAT and cash conversion separately; the "FY26 Cash conversion" chip
  opens CFO ₹7,088 crore and PAT ₹4,395 crore (FY26, consolidated) with the formula and AR pp.236/110
  (`m14-claim-view-cash-conversion.jpg`); "Open page 236" opens the annual report at that line.
* No-AI answers for ASIANPAINT, HDFCBANK, TCS, INFY, RELIANCE, TITAN: 0 statements removed (91 figures checked).
* AI answer to "Why did EBITDA margin fall in Q2FY27?" kept the cause because the cited call passages mention
  inflation and staggered price increases; it removed one sentence where the model computed a sequential change
  itself ("dropped 5.0 pp"), which no registered calculation backs.

## What the checks do not prove

The validator confirms that a figure is the cited figure (value, sign, unit, period, basis, company) and that a stated
cause shares words with a cited passage. It does not establish that the passage actually supports the causal reading,
that an interpretation is sound, or that the standardised source transcribed the filing correctly. Citation existence
is never treated as semantic verification.

## Remaining limitations

* Word-overlap is a weak test of causal support; a passage can mention "costs" without attributing the change to them.
* Metric and sign detection are rule-based English patterns; unusual phrasing ("PAT halved", "margin compressed by
  200 bps to …") may be removed even when correct (removal is the safe failure).
* Primary-page matching needs the line label and the value in the same passage; it fails for scanned reports, other
  units (₹ lakh) or tables split across chunks, in which case only the secondary source is shown.
* Model-computed figures (e.g. a sequential pp change) are removed rather than recomputed; only YoY/QoQ growth,
  YoY margin change, cash conversion, D/E, CAGRs and price moves are registered calculations.
* The capital-event parser covers common Indian disclosure wording; other instruments (InvITs, ESOP allotments,
  conversions of warrants into shares) default to "Disclosure" stage.
* Allotment amounts are not computed from unit count × price, so the warrant allotment shows "Amount: not stated".
* Research answers saved in a browser session before this change keep their old figures without "printed as" values
  until asked again.
