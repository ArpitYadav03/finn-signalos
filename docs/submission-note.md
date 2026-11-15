# Covering note — draft (not sent)

**To:** assessment@enam.com
**Subject:** IIT Delhi – AI Engineer - 2027

Hello ENAM team,

Please find my submission for the FINN build challenge.

* Prototype: **[deployed URL — add after deploying, see docs/deployment.md]**
* Repository: **[GitHub URL]**
* AI build history: **[Claude Code transcript link]** (summary in `docs/ai-build-history.md`)

**What I built.** FINN SignalOS — "signal first, evidence always". A mobile-first briefing that ranks every
filing across an 18-stock portfolio, explains *why* each one matters, collapses the routine, and flags unusual
end-of-day moves. One tap on *Investigate* opens research on that company with the filing in context, answered
only from annual reports, call transcripts and filed results — every claim opens to its page. The piece I'm
proudest of is the **Management Promise Tracker**: it pulls guidance said by management (not analysts) from
transcripts and checks it against reported numbers, e.g. Infosys's FY27 operating-margin guidance of 20–22%
vs 21.0% in Q2 (on track), and it refuses comparisons that aren't like-for-like (constant currency vs INR,
segment vs company, EBITDA for banks).

**Key decisions.**
1. *Deterministic first.* All arithmetic, ranking, alert evaluation and period matching is Python. Models are
   used only where judgment helps, behind a provider-agnostic client with call caps and cost tracking. Without
   a key the app is fully functional at $0 — and says so.
2. *Explainable, size-aware materiality.* Every score shows its components; important categories can never be
   hidden; a penalty is judged against the company's revenue (a ₹1.6 lakh fine doesn't page you).
3. *Briefing-first UX with one-tap Push → Pull*, instead of a chat box as the front door.
4. *Primary sources only for Pull*, with verbatim, page-level evidence.

**Trade-offs and what I left out.** The filing feed is the latest ~5 filings per company from Screener's
document index (NSE blocks automated access; I didn't try to bypass it). Structured numbers are Screener's
standardisation of filed results, clearly labelled. Guidance extraction is rules-based for precision; the
LLM extractor (quote-verified) is implemented but I couldn't run it without a key. Not built: narrative-drift
detection across calls, push notifications, incoming-executive bios beyond what the filing states, multi-user.

**Next.** Official filings feed; LLM extraction with the existing verbatim-quote check; drift detection between
consecutive calls; pgvector in SQL; web push for alerts; per-user portfolios.

Thank you,
[Your name]


## Final status (15 Nov 2026)

Status: **READY WITH DISCLOSED LIMITATIONS** (updated later on 15 Nov: CIPLA, INFY and ASIANPAINT fixed; the limitations below remain and the submission links are missing). Original note: frontend work is complete and verified, but backend correctness defects remained user-visible (ASIANPAINT management card, L&T comparability, indexed-mode replay guidance cutoff; CIPLA and INFY were fixed later the same day). Full evidence, repair specifications and verification are in `docs/submission-status.md` and `docs/review-fixes-and-backend-spec.md`. Deployment, repository and build-history URLs are missing.
