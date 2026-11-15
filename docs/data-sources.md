# Data sources — what is live, cached or sample

| Data | Source | Mode | Freshness shown in UI |
|---|---|---|---|
| EOD price & volume | Yahoo Finance via `yfinance` (`TICKER.NS`), unadjusted close | **Live** on each ingestion run | "prices EOD <date>" + fetch time |
| Corporate filings | BSE filings listed on each company's Screener.in page (title, ISO timestamp, one-line subject, link to the BSE PDF) | **Live**, cached 6h on disk | "filings checked <ago>"; each card shows exchange publication time |
| Filing PDFs | `bseindia.com` (AnnPdfOpen → AttachLive/AttachHis) | **Live**, downloaded on demand, content-hashed | Evidence Lens links to the original |
| Annual reports, transcripts, decks | Links on the Screener page → BSE / company IR PDFs | **Live**, downloaded on demand, cached | Sources tab shows indexed / failed |
| Quarterly & annual results table | Screener's standardisation of the company's filed results (consolidated when available) | **Live**, replaced on refresh | Labelled "filed results (standardised)" with a link |
| Synthetic test data | `backend/tests/*` (company `FAKECO`) | **Sample**, tests only | Flagged `is_sample` |

## Compliance notes

* Honest user agent (`FINN-SignalOS/0.1 research prototype`), ≥1 s between requests per host, on-disk cache,
  stale-cache fallback when a source is down.
* Screener's robots.txt allows company pages; FINN does not use disallowed paths (search `?q=`, login-only
  "important announcements").
* NSE's website/API rejects automated clients (403) — FINN does not try to bypass it. BSE's own announcement API
  was not used for the same reason; adding an official or licensed feed only requires a new `AnnouncementProvider`.
* Document downloads are restricted to exchange hosts, or to company IR hosts discovered from the document
  listing (public IPs only, https only, 40 MB / 400 pages caps).
* No LinkedIn or other people-data scraping. Management profiles come from the Reg-30 filing when it includes one.

## Primary vs secondary

Pull answers cite only annual reports, transcripts, exchange filings and filed results. The one-line filing
subject shown on Push cards is the listing's summary (Screener) and is labelled as such; it is never used as
evidence in Research.

## Units, periods, bases

* All monetary values normalised to INR crore (`finance.to_crore` handles rupees, lakh, million, billion).
* Indian fiscal year (FY27 = Apr 2026 – Mar 2027); quarters labelled `Q2FY27`.
* Statement basis (consolidated/standalone) stored with every metric and shown on cards.
* Lenders (banks/NBFCs) are detected from the results layout: EBITDA, debt/equity and CFO/PAT are suppressed.
* Restatements: metrics are replaced wholesale on each refresh, so restated history overwrites old values.
