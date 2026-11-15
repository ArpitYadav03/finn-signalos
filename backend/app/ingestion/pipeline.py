"""Ingestion orchestration. Every step is idempotent: re-running never duplicates rows or re-analyses
unchanged documents (URL + content-hash keyed)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.ingestion.http import FetchError, download_pdf
from app.ingestion.providers import ScreenerCompanyProvider, YahooMarketProvider, normalise_ticker
from app.ingestion.screener_parser import ParsedAnnouncement, ParsedCompany
from app.models import (
    Announcement,
    Company,
    Document,
    DocumentChunk,
    FinancialMetric,
    IngestionRun,
    ManagementGuidance,
    Portfolio,
    PortfolioHolding,
    Price,
    UserPreference,
)
from app.rag.chunker import chunk_document, chunk_transcript
from app.rag.pdf import extract_pages
from app.services.classifier import classify
from app.services.guidance import dominant_margin_metric, extract_guidance
from app.services.periods import to_utc_naive

log = logging.getLogger(__name__)
PORTFOLIO_FILE = Path(__file__).resolve().parents[1] / "data" / "portfolio.json"
market_provider = YahooMarketProvider()
company_provider = ScreenerCompanyProvider()


# ------------------------------------------------------------------ seed


def seed(db: Session) -> Portfolio:
    data = json.loads(PORTFOLIO_FILE.read_text())
    pf = db.scalars(select(Portfolio).where(Portfolio.name == data["name"])).first()
    if pf is None:
        pf = Portfolio(name=data["name"])
        db.add(pf)
        db.flush()
    for row in data["holdings"]:
        c = db.scalars(select(Company).where(Company.ticker == row["ticker"])).first()
        if c is None:
            c = Company(ticker=row["ticker"], company_name=row["name"], sector=row["sector"],
                        market_cap_category=row["size"], is_financial=row.get("financial", False))
            db.add(c)
            db.flush()
        if not db.scalars(select(PortfolioHolding).where(PortfolioHolding.portfolio_id == pf.id,
                                                         PortfolioHolding.company_id == c.id)).first():
            db.add(PortfolioHolding(portfolio_id=pf.id, company_id=c.id))
    defaults = {
        "thresholds": {"price_move_pct": get_settings().default_price_move_pct,
                       "volume_multiple": get_settings().default_volume_multiple},
        "watched_categories": {"categories": ["results", "M&A", "management_change", "litigation", "credit_rating",
                                              "fund_raise", "pledging"]},
    }
    for k, v in defaults.items():
        if not db.scalars(select(UserPreference).where(UserPreference.key == k)).first():
            db.add(UserPreference(key=k, value=v))
    db.commit()
    return pf


def portfolio_companies(db: Session) -> list[Company]:
    return list(db.scalars(select(Company).join(PortfolioHolding, PortfolioHolding.company_id == Company.id)
                           .order_by(Company.ticker)).all())


# ------------------------------------------------------------------ company index


def upsert_company(db: Session, ticker: str, *, force: bool = False,
                   cache_hours: float | None = None) -> tuple[Company, ParsedCompany]:
    ticker = normalise_ticker(ticker)
    parsed, url, _cached = company_provider.fetch_company(ticker, force=force, cache_hours=cache_hours)
    c = db.scalars(select(Company).where(Company.ticker == ticker)).first()
    if c is None:
        c = Company(ticker=ticker, company_name=parsed.name)
        db.add(c)
        db.flush()
    c.company_name = parsed.name or c.company_name
    c.about = parsed.about
    c.bse_code = parsed.bse_code
    c.is_financial = parsed.is_financial

    db.flush()
    # Versioned: the fetch becomes a snapshot; changed values are new observations (restatements keep history); the
    # snapshot is validated before financial_metrics is updated. A rejected snapshot leaves served data untouched.
    from app.services.financial_history import Candidate, record_snapshot

    record_snapshot(db, c, [Candidate(m.fiscal_period, m.period_end, m.period_type, m.metric_name, m.value, m.unit)
                            for m in parsed.metrics], basis=parsed.basis, source="screener:filed_results", source_url=url)
    seen_urls: set[str] = set()
    for d in parsed.documents:
        if d.url in seen_urls:  # Screener can list the same PDF under two headings
            continue
        seen_urls.add(d.url)
        existing = db.scalars(select(Document).where(Document.company_id == c.id, Document.source_url == d.url)).first()
        if existing is None:
            db.add(Document(company_id=c.id, document_type=d.document_type, title=d.title,
                            reporting_period=d.period_label, source_url=d.url, publication_date=d.period_date))
    db.commit()
    ingest_announcements(db, c, parsed.announcements, source="screener:bse")
    record_listing_coverage(db, c, [to_utc_naive(a.published_at) for a in parsed.announcements])
    return c, parsed


def record_listing_coverage(db: Session, company: Company, published: list[datetime], *, error: str | None = None,
                            source: str = "filings_listing") -> None:
    """Remember what the (latest-N) filings listing actually covered. If a new listing's oldest item is newer than the
    previous listing's newest item, filings in between may have been missed: continuity restarts there."""
    from app.models import SourceCoverage

    cov = db.scalars(select(SourceCoverage).where(SourceCoverage.company_id == company.id, SourceCoverage.source == source)).first()
    if cov is None:
        cov = SourceCoverage(company_id=company.id, source=source, limited_window=True)
        db.add(cov)
    now = utcnow()
    cov.last_attempt_at = now
    if error is not None:
        cov.last_error = error[:300]
        db.commit()
        return
    cov.last_success_at, cov.last_error, cov.items_last = now, None, len(published)
    if published:
        oldest, newest = min(published), max(published)
        if cov.newest_item_at is None or cov.continuous_since is None or oldest > cov.newest_item_at:
            cov.continuous_since = oldest      # first listing, or a gap since the last one
        cov.oldest_item_at = oldest
        cov.newest_item_at = max(newest, cov.newest_item_at or newest)
    elif cov.continuous_since is None:
        cov.continuous_since = None
    db.commit()


def _event_group(company_id: int, title: str, published: datetime) -> str:
    cat = classify(title).category.value
    day = (published + timedelta(hours=5, minutes=30)).date().isoformat()
    if cat in ("results", "earnings_call", "dividend"):
        key = f"{company_id}:{cat}:{day}"  # outcome + press release + deck on the same day = one event
    else:
        norm = re.sub(r"[^a-z]+", " ", title.lower()).strip()
        norm = re.sub(r"\b(intimation|outcome|disclosure|update|regulation \d+|lodr|announcement under)\b", "", norm)
        # Same title on the same day = one event. Recurring titles on different days ("Intimation of Repayment of
        # Commercial Paper") are different events unless their facts say otherwise (see filing_agent.find_same_event).
        key = f"{company_id}:{cat}:{day}:{' '.join(norm.split()[:8])}"
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def ingest_announcements(db: Session, company: Company, items: list[ParsedAnnouncement], *, source: str,
                         is_sample: bool = False) -> list[int]:
    new_ids = []
    for a in items:
        h = hashlib.sha256(a.url.strip().encode()).hexdigest()
        if db.scalars(select(Announcement.id).where(Announcement.company_id == company.id,
                                                    Announcement.content_hash == h)).first():
            continue
        pub = to_utc_naive(a.published_at)
        row = Announcement(company_id=company.id, title=a.title, exchange_summary=a.summary, source=source,
                           source_url=a.url, published_at=pub, content_hash=h, is_sample=is_sample,
                           event_group=_event_group(company.id, a.title, pub))
        db.add(row)
        db.flush()
        new_ids.append(row.id)
    db.commit()
    return new_ids


# ------------------------------------------------------------------ market


def refresh_market(db: Session, companies: list[Company], days: int = 45) -> dict:
    bars = market_provider.fetch_eod([c.ticker for c in companies], days=days)
    stats = {"companies": len(companies), "bars": 0, "missing": []}
    for c in companies:
        rows = bars.get(c.ticker) or []
        if not rows:
            stats["missing"].append(c.ticker)
            continue
        existing = {p.trading_date: p for p in db.scalars(select(Price).where(Price.company_id == c.id)).all()}
        for b in rows:
            p = existing.get(b.trading_date)
            if p is None:
                db.add(Price(company_id=c.id, trading_date=b.trading_date, open=b.open, high=b.high, low=b.low,
                             close=b.close, volume=b.volume, source=market_provider.name))
                stats["bars"] += 1
            else:  # provider may revise the latest bar after close
                p.close, p.volume, p.open, p.high, p.low, p.fetched_at = b.close, b.volume, b.open, b.high, b.low, utcnow()
    db.commit()
    return stats


# ------------------------------------------------------------------ documents


def _store_chunks(db: Session, doc: Document, pages: list[tuple[int, str]]) -> int:
    from app.llm.client import get_llm

    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    chunks = chunk_transcript(pages) if doc.document_type == "concall_transcript" else chunk_document(pages)
    rows = [DocumentChunk(document_id=doc.id, chunk_index=i, page_number=c.page_number, section=c.section,
                          speaker=c.speaker, speaker_role=c.speaker_role, content=c.content)
            for i, c in enumerate(chunks)]
    llm = get_llm()
    if llm and rows:
        try:
            for r, e in zip(rows, llm.embed([r.content for r in rows])):
                r.embedding = e
        except Exception as e:  # embeddings are an enhancement; BM25 still works
            log.warning("Embedding failed for doc %s: %s", doc.id, e)
    db.add_all(rows)
    return len(rows)


def index_document(db: Session, doc: Document, *, force: bool = False, owner: str | None = None) -> Document:
    """Index one document through its durable job (key ``doc:<id>``).

    Finished work is reused; a job running in another worker is left alone (no duplicate download); a job in backoff is
    not retried early unless ``force``; terminal failures stay failed with their explanation."""
    from app.services import jobs

    if doc.status == "indexed" and not force:
        return doc
    job = jobs.ensure_job(db, "index_document", f"doc:{doc.id}", company_id=doc.company_id, document_id=doc.id)
    if force and job.status in ("succeeded", "failed", "retry"):
        jobs.requeue(db, job)
    elif job.status == "failed":
        return doc
    me = owner or jobs.worker_id("inline")
    if not jobs.claim(db, job.id, me):
        db.refresh(doc)
        return doc          # running elsewhere, or waiting out its backoff
    return run_claimed_document_job(db, doc, job, me)


def run_claimed_document_job(db: Session, doc: Document, job, me: str) -> Document:
    """Execute a document job whose lease ``me`` already holds."""
    from app.services import jobs

    try:
        result = _index_document_body(db, doc, job, me)
        jobs.succeed(db, job, me, result)
    except Exception as e:  # noqa: BLE001 — every failure is classified and recorded, never swallowed silently
        status = jobs.fail(db, job, me, e)
        db.refresh(doc)
        doc.status = "failed" if status == "failed" else "retrying"
        doc.error = job.error
        db.commit()
        log.warning("Indexing %s for %s: %s", status, doc.source_url, e)
    db.refresh(doc)
    return doc


def _index_document_body(db: Session, doc: Document, job, owner: str) -> dict:
    from app.services import jobs

    s = get_settings()
    result: dict = {}
    # 1. Download — reuse the stored file when a previous attempt already fetched it.
    jobs.progress(db, job, owner, "download")
    path = Path(doc.storage_path) if doc.storage_path else None
    if path is not None and path.exists() and doc.content_hash:
        result["download"] = "reused"
    else:
        company_ir = doc.document_type in ("concall_transcript", "presentation", "annual_report")
        path, digest = download_pdf(doc.source_url, s.data_dir / "docs" / doc.company.ticker, company_ir_host=company_ir)
        doc.content_hash, doc.storage_path = digest, str(path)
        db.commit()        # persisted before extraction, so a later retry does not download again
        result["download"] = "fetched"
    # 2. Extract text.
    jobs.progress(db, job, owner, "extract")
    pages = extract_pages(path, max_pages=s.max_pdf_pages)
    doc.page_count = len(pages)
    db.commit()
    if not pages:
        raise jobs.TerminalError("no_text", "No extractable text (scanned PDF?)")
    # 3. Chunk (+ embeddings when available).
    jobs.progress(db, job, owner, "chunk", pages=len(pages))
    result["chunks"] = _store_chunks(db, doc, pages)
    doc.status, doc.error = "indexed", None
    db.commit()
    # 4. Analyse — transcripts feed the Promise Ledger.
    if doc.document_type == "concall_transcript":
        jobs.progress(db, job, owner, "analyse")
        result["guidance_items"] = extract_document_guidance(db, doc)
    result["pages"] = len(pages)
    log.info("Indexed %s (%d pages, %d chunks)", doc.title, len(pages), result["chunks"])
    return result


def index_filing_pdf(db: Session, ann: Announcement) -> Document | None:
    doc = db.scalars(select(Document).where(Document.company_id == ann.company_id,
                                            Document.source_url == ann.source_url)).first()
    if doc is None:
        doc = Document(company_id=ann.company_id, document_type="exchange_filing", title=ann.title[:300],
                       source_url=ann.source_url, publication_date=ann.published_at.date())
        db.add(doc)
        db.flush()
    if doc.status != "indexed":
        index_document(db, doc)
    return doc if doc.status == "indexed" else None


def _period_end(label: str) -> date | None:
    from app.services.periods import FiscalQuarter

    m = re.match(r"^Q([1-4])FY(\d{2})$", label)
    if m:
        return FiscalQuarter(2000 + int(m.group(2)), int(m.group(1))).end_date
    m = re.match(r"^FY(\d{2})$", label)
    if m:
        return date(2000 + int(m.group(1)), 3, 31)
    return None


def extract_document_guidance(db: Session, doc: Document) -> int:
    """Rules-v1 guidance extraction over management turns, using the preceding analyst question as context."""
    db.execute(delete(ManagementGuidance).where(ManagementGuidance.source_document_id == doc.id))
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
                        .order_by(DocumentChunk.chunk_index)).all()
    ref = doc.publication_date or date.today()
    full = " ".join(c.content for c in chunks)
    default_margin = dominant_margin_metric(full)
    # Companies that guide in constant currency say so repeatedly; a single sentence may omit it.
    cc_call = len(re.findall(r"constant currency", full, re.I)) >= 3
    prev_q, seen, n = None, set(), 0
    kept: dict[tuple, ManagementGuidance] = {}
    for idx, c in enumerate(chunks):
        if c.speaker_role == "analyst":
            prev_q = c.content
            continue
        if c.speaker_role != "management":
            continue
        text = c.content
        # Chunks overlap: a sentence cut at a chunk's end ("guidance remains at 20%") is complete in the next chunk.
        if idx + 1 < len(chunks) and not re.search(r"[.!?\"”)]\s*$", text.strip()):
            text = re.sub(r"[^.!?]*$", "", text)
        for g in extract_guidance(text, ref, question_context=prev_q, default_margin=default_margin):
            if g.guidance_type == "qualitative" and g.metric.endswith("_unspecified"):
                continue
            if cc_call and g.metric == "revenue_growth" and g.basis == "reported":
                g.basis = "constant_currency"
                g.conditions = (g.conditions + "; " if g.conditions else "") + \
                    "basis inferred: this call frames growth guidance in constant currency"
            if g.target_period and _period_end(g.target_period) and _period_end(g.target_period) < ref:
                continue  # "we exited FY26 at 25%" said after FY26 ended is a report, not a promise
            norm = re.sub(r"\W+", " ", g.statement.lower()).strip()
            # Overlapping chunks repeat the same sentence (sometimes truncated): keep the most complete copy per metric.
            dup = next(((k, m) for k, m in seen if (norm in k or k in norm) and m == g.metric), None)
            if dup is not None:
                if len(norm) <= len(dup[0]):
                    continue
                seen.discard(dup)
                old = kept.pop(dup, None)
                if old is not None:
                    db.expunge(old) if old in db.new else db.delete(old)
                    n -= 1
            seen.add((norm, g.metric))
            row = ManagementGuidance(
                company_id=doc.company_id, metric=g.metric, guidance_type=g.guidance_type,
                target_value=g.target_value, target_min=g.target_min, target_max=g.target_max,
                comparator=g.comparator, unit=g.unit, basis=g.basis, horizon=g.horizon, target_period=g.target_period,
                guidance_date=ref, statement=g.statement, speaker=c.speaker, conditions=g.conditions,
                source_document_id=doc.id, source_page=c.page_number, chunk_id=c.id, extractor="rules-v1")
            db.add(row)
            kept[(norm, g.metric)] = row
            n += 1
    db.commit()
    return n


def ensure_research_corpus(db: Session, company: Company, *, transcripts: int = 4, annual_reports: int = 1) -> dict:
    """Index the latest N transcripts and annual reports for a company (cached after first run)."""
    out = {"indexed": [], "failed": [], "skipped": []}

    def latest(dtype: str, n: int) -> list[Document]:
        docs = db.scalars(select(Document).where(Document.company_id == company.id, Document.document_type == dtype)).all()
        docs = sorted(docs, key=lambda d: d.publication_date or date.min, reverse=True)
        # Screener lists one entry per source (bse/nse); keep one per period.
        uniq, periods = [], set()
        for d in docs:
            if d.reporting_period in periods:
                continue
            periods.add(d.reporting_period)
            uniq.append(d)
        return uniq[:n]

    out["in_progress"] = []
    for d in latest("concall_transcript", transcripts) + latest("annual_report", annual_reports):
        before = d.status
        index_document(db, d)
        row = {"title": d.title, "status": d.status, "error": d.error, "cached": before == "indexed"}
        if d.status == "indexed":
            out["indexed"].append(row)
        elif d.status in ("discovered", "retrying"):
            out["in_progress"].append(row)   # being processed by another worker, or waiting to retry
        else:
            out["failed"].append(row)
    return out


# ------------------------------------------------------------------ orchestration


def process_pending(db: Session, limit: int = 200) -> int:
    from app.agents.filing_agent import analyze_announcement

    pending = db.scalars(select(Announcement).where(Announcement.processing_status == "pending")
                         .order_by(Announcement.published_at).limit(limit)).all()
    n = 0
    for a in pending:
        try:
            analyze_announcement(db, a)
            n += 1
        except Exception as e:
            log.exception("Analysis failed for announcement %s", a.id)
            db.rollback()
            a.processing_status = "failed"
            db.commit()
    return n


ROUTINE_FOR_INDEXING = {"unclassified", "investor_meetings"}


def refresh_documents_for(db: Session, companies: list[Company], *, filing_days: int = 7, max_docs: int | None = None) -> dict:
    """Bring Research up to date: full text of recent non-routine filings, plus any newly published call transcript
    or annual report (already-indexed documents are skipped, so steady-state runs are cheap). Transcripts feed the
    Promise Ledger automatically on indexing."""
    s = get_settings()
    budget = max_docs if max_docs is not None else s.daily_max_new_documents
    out = {"filings_indexed": 0, "transcripts_indexed": 0, "annual_reports_indexed": 0, "failed": 0, "retry_scheduled": 0,
           "waiting_retry": 0, "skipped_budget": 0}

    def not_due(doc: Document | None) -> bool:
        from app.models import IngestionJob

        if doc is None or doc.status != "retrying":
            return False
        job = db.scalars(select(IngestionJob).where(IngestionJob.key == f"doc:{doc.id}")).first()
        return bool(job and job.status == "retry" and job.next_attempt_at > utcnow())

    def tally(doc: Document | None, ok_key: str) -> None:
        st = doc.status if doc is not None else "failed"
        out[ok_key if st == "indexed" else "retry_scheduled" if st == "retrying" else "failed"] += 1
    since = utcnow() - timedelta(days=filing_days)
    for c in companies:
        recent = db.scalars(select(Announcement).where(Announcement.company_id == c.id, Announcement.published_at >= since,
                                                       Announcement.processing_status == "processed")).all()
        for a in recent:
            if a.category in ROUTINE_FOR_INDEXING:
                continue
            existing = db.scalars(select(Document).where(Document.company_id == c.id, Document.source_url == a.source_url)).first()
            if existing is not None and existing.status in ("indexed", "failed"):
                continue
            if not_due(existing):
                out["waiting_retry"] += 1   # transient failure in backoff: no download until its retry time
                continue
            if budget <= 0:
                out["skipped_budget"] += 1
                continue
            budget -= 1
            try:
                index_filing_pdf(db, a)
                tally(db.scalars(select(Document).where(Document.company_id == c.id, Document.source_url == a.source_url)).first(),
                      "filings_indexed")
            except Exception as e:
                db.rollback()
                out["failed"] += 1
                log.warning("Filing index failed for %s: %s", a.id, e)
        for dtype, n, key in (("concall_transcript", s.daily_transcripts_per_company, "transcripts_indexed"),
                              ("annual_report", 1 if s.daily_index_annual_reports else 0, "annual_reports_indexed")):
            if n <= 0:
                continue
            docs = sorted(db.scalars(select(Document).where(Document.company_id == c.id, Document.document_type == dtype)).all(),
                          key=lambda d: d.publication_date or date.min, reverse=True)
            seen_periods: set = set()
            for d in docs:
                if d.reporting_period in seen_periods:
                    continue
                seen_periods.add(d.reporting_period)
                if len(seen_periods) > n:
                    break
                if d.status in ("indexed", "failed"):
                    continue
                if not_due(d):
                    out["waiting_retry"] += 1
                    continue
                if budget <= 0:
                    out["skipped_budget"] += 1
                    continue
                budget -= 1
                index_document(db, d)
                tally(d, key)
    return out


def run_daily(db: Session, *, refresh_documents: bool = False, index_documents: bool = True) -> IngestionRun:
    from app.services.alerts import evaluate_all

    run = IngestionRun(job="daily")
    db.add(run)
    db.commit()
    stats: dict = {}
    errors: list = []
    companies = portfolio_companies(db)
    stats["market"] = refresh_market(db, companies)
    new_ann = 0
    for c in companies:
        try:
            before = db.query(Announcement).filter(Announcement.company_id == c.id).count()
            upsert_company(db, c.ticker, force=refresh_documents)
            new_ann += db.query(Announcement).filter(Announcement.company_id == c.id).count() - before
        except Exception as e:
            db.rollback()
            errors.append({"ticker": c.ticker, "error": str(e)[:300]})
            record_listing_coverage(db, c, [], error=str(e))
            log.warning("Company refresh failed for %s: %s", c.ticker, e)
    stats["new_announcements"] = new_ann
    stats["analysed"] = process_pending(db)
    if index_documents:
        try:
            stats["documents"] = refresh_documents_for(db, companies)
        except Exception as e:  # document refresh must never block prices, filings or alerts
            db.rollback()
            errors.append({"step": "documents", "error": str(e)[:300]})
            log.exception("Document refresh failed")
    stats["alerts"] = evaluate_all(db)
    try:
        from app.services.monitoring import evaluate_enabled

        from app.services.hypotheses import refresh_all

        stats["investigations"] = refresh_all(db)   # before plans, so changed evidence reaches the briefing
        stats["plans"] = evaluate_enabled(db)
    except Exception as e:  # a plan evaluation failure must not hide the refresh result
        db.rollback()
        errors.append({"step": "plans", "error": str(e)[:300]})
        log.exception("Plan evaluation failed")
    run.stats, run.errors, run.finished_at = stats, errors, utcnow()
    run.status = "ok" if not errors else "partial"
    db.commit()
    return run
