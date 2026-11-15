# Covering email (draft — fill in the three links, then send yourself)

**To:** assessment@enam.com
**Subject:** IIT Delhi – AI Engineer - 2027

---

Dear ENAM team,

Please find my submission for the AI Engineer build challenge.

- **Prototype (works on a phone):** [DEPLOYED LINK — missing until deployed]
- **GitHub repository:** [GITHUB LINK — missing until pushed]
- **AI build history:** [SHARE LINK to the Claude Code session export — e.g. a Google Drive link to session-export-….zip]; a summary is in `docs/ai-build-history.md` in the repository.

**What I built.** FINN SignalOS is a mobile-first investment-intelligence app for an 18-stock NSE portfolio, built on one rule: every result says what happened, why it matters, the evidence, what is uncertain and what to do next.
- *Push:* a 24-hour digest of exchange filings, classified and scored for materiality, with end-of-day price/volume context, related filings grouped into one event, and repeats suppressed.
- *Pull:* research on any NSE symbol, answered only from the company's own filings, annual reports and call transcripts. Every figure carries its period, basis, formula and source.
- *Monitoring plans:* plain words become a reviewed, versioned plan (scope, thresholds, direction, AND/OR). The return briefing shows "changes since your last review".
- *Historical replay* tests a plan on past data with point-in-time rules.
- *"Evidence for and against"* tests competing explanations, currently for cash-conversion questions.
- *Promise Ledger* checks management guidance against delivered results. For example, Infosys FY26 margin guidance of 20–22% vs 20.92% delivered = (42,280 − 4,902) ÷ 178,650.

**Key decisions and trade-offs.**
- The model never does arithmetic. All calculations are deterministic and tested, and claims are verified against their sources for value, sign, unit, period and basis.
- Primary sources only, no web or news search: fewer answers, but each one is checkable.
- A deterministic core with bounded model calls: the app works without an AI key, and AI output is quote-checked or discarded.
- Durable ingestion jobs with retries and recovery. The filings source lists only each company's latest filings, so coverage gaps are shown, never hidden.

**Deliberately left out.** Buy/sell advice, intraday data, news and social sources, user accounts (one shared workspace behind an optional password), and broad "for and against" question types beyond cash conversion.

**Known limitations (disclosed in the app).** One AI-classified passage in the L&T investigation covers a different period and part of the business (the conclusion is unchanged on computed evidence alone). In the optional "indexed" replay mode, guidance cuts use the statement date. Price history in the demo starts on 8 Sep 2026.

**Where I'd take it next.** A licensed full filings feed; extracting half-year cash flows; broader question types for Competing Hypotheses; push notifications for plans; individual accounts.

Verification: 157 backend tests and 13 frontend tests pass; type-check, lint and production build are clean; eight end-to-end journeys checked in the browser at phone, tablet and desktop widths.

Best regards,
Arpit Yadav
IIT Delhi
