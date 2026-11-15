# ADR-010: Research stays primary-source only — no web search or news

**Status:** accepted (confirmed by the product owner) · **Date:** 2026-11-15

**Context.** With an LLM connected, users asked why Research answers "latest updates" from company documents rather
than the internet. A web-search tool would be easy to add.

**Decision.** Research (Pull) uses only the company's own disclosures: exchange filings, annual reports, earnings-call
transcripts, filed results, plus end-of-day prices. No web search, news, broker notes or model background knowledge.

**Why.**
* The ENAM brief requires it for the Pull side ("No broker notes, no news, no research reports, and don't lean on the
  model's own outside knowledge") and evaluators will check it.
* Exchange filings are the earliest primary record of material events — news follows them — and FINN re-checks a
  company's filing index at the source when its copy is older than 15 minutes, so "latest" answers are current.
* Every claim stays verifiable through the Evidence Lens; web content would break the citation and grounding checks.

**Consequences.** Coverage of "current events" is bounded by what companies file and by the filing index (≈ the latest
5 filings per company). The compliant extension is an official or licensed exchange feed behind the existing
`AnnouncementProvider` interface, not a news API. A separate, clearly labelled news panel was considered and declined.
