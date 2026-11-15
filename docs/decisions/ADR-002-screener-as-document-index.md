# ADR-002: Use the Screener company page as a document index

**Status:** accepted

**Context.** NSE's site and API return 403 to automated clients. BSE's JSON API is undocumented. The brief points to Screener for annual reports and transcripts.

**Decision.** A `ScreenerCompanyProvider` reads one allowed page per company (robots.txt permits `/company/*`) to get: the latest filings with ISO timestamps and links to the original BSE PDFs, annual-report and transcript links, and the standardised filed-results tables. Every cited document is the original exchange/IR PDF.

**Consequences.** Reliable and low-rate, works for any NSE symbol. Only ~5 latest filings per company are visible without login, so a 30-day window can miss older filings for busy companies. The provider interface (`CompanyDataProvider`, `FixtureAnnouncementProvider`) lets an official feed replace it.
