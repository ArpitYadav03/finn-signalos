from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.agents.filing_agent import category_label
from app.agents.research_agent import research
from app.api.deps import company_or_404, rate_limit, require_admin
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db, utcnow
from app.domain.categories import CATEGORY_LABELS, Category
from app.ingestion import pipeline
from app.ingestion.http import FetchError
from app.ingestion.providers import normalise_ticker
from app.models import (
    AgentRun, Alert, AlertRule, Announcement, Company, Document, DocumentChunk, IngestionRun, Portfolio,
    EventAnalysis, ManagementGuidance, PortfolioHolding, Price, UserPreference,
)
from app.schemas import (
    ApplyActionsRequest, AssistantRequest,
    AlertRuleCreate, AlertRuleParseRequest, AlertRuleUpdate, AlertStatusUpdate, Briefing, PreferencesUpdate,
    ResearchRequest, ResearchResponse, SignalCard,
)
from app.services import alerts as alert_svc
from app.services import market as market_svc
from app.services.metrics import annual_overview, load_series, results_snapshot
from app.services.promise import promise_tracker

router = APIRouter(prefix="/api/v1")
LEVELS = ["routine", "low", "medium", "high"]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _prefs(db: Session) -> dict:
    rows = {p.key: p.value for p in db.scalars(select(UserPreference)).all()}
    th = rows.get("thresholds", {})
    return {"price_move_pct": th.get("price_move_pct", 4.0), "volume_multiple": th.get("volume_multiple", 2.0),
            "watched_categories": rows.get("watched_categories", {}).get("categories", [])}


def _card(a: Announcement, db: Session, related: list[Announcement] | None = None) -> SignalCard:
    ea = a.analysis
    members = [a, *(related or [])]
    best = max(members, key=lambda m: m.analysis.importance_score if m.analysis else -1)
    score, level, reasons = ea.importance_score, ea.importance_level, list(ea.reasoning)
    if len(members) > 1 and best.analysis and best.analysis.importance_score > score:
        # An event is as important as its most important filing; say so explicitly.
        score, level, reasons = best.analysis.importance_score, best.analysis.importance_level, list(best.analysis.reasoning)
    if len(members) > 1:
        reasons.append({"label": f"Event groups {len(members)} filings; score = highest among them", "points": 0})
    cites = list(ea.citations)
    for r in related or []:
        if r.analysis:
            first = next((c for c in r.analysis.citations if c.get("kind") == "filing"), None)
            if first and all(c.get("source_url") != first.get("source_url") for c in cites):
                cites.append(first)
    timing = market_svc.event_timing(db, a.company_id, a.published_at)
    rx = timing["reaction"]
    return SignalCard(
        id=a.id, ticker=a.company.ticker, company_name=a.company.company_name, category=ea.category,
        category_label=category_label(ea.category), headline=ea.headline, summary=ea.summary,
        why_it_matters=ea.why_it_matters, importance_score=score, importance_level=level,
        reasons=reasons, key_metrics=ea.key_metrics, facts=ea.facts, citations=cites,
        published_at=_iso(a.published_at), processed_at=_iso(ea.created_at), source=a.source, source_url=a.source_url,
        is_sample=a.is_sample, requires_review=ea.requires_review, analysis_method=ea.model_name,
        what_changed=ea.facts.get("what_changed") or ea.summary,
        supporting=_event_dna(members),
        event_key=a.event_group or f"solo{a.id}",
        milestones=_milestones(members),
        promise_links=_promise_links(db, members),
        related=[{"id": r.id, "title": (r.analysis.facts.get("subject") if r.analysis else None) or r.title,
                  "source_url": r.source_url, "published_at": _iso(r.published_at)} for r in (related or [])],
        # Only the first session that traded entirely after publication is shown as "after the filing"; context
        # before it (and a split session for filings made during trading) is labelled as such.
        market={"trading_date": rx["date"], "return_pct": rx["return_pct"], "volume_ratio": rx["volume_ratio"],
                "relation": timing["relation"], "label": timing["label"], "pre_event": timing["pre_event"],
                "mixed_session": timing["mixed_session"], "caution": timing["caution"]} if rx else
               {"trading_date": None, "return_pct": None, "volume_ratio": None, "relation": timing["relation"],
                "label": timing["label"], "pre_event": timing["pre_event"], "mixed_session": timing["mixed_session"],
                "caution": timing["caution"]},
    )


def _milestones(members: list[Announcement]) -> list[dict]:
    from app.services.milestones import next_evidence, resolved_by

    ordered = sorted(members, key=lambda m: m.published_at)
    latest = ordered[-1]
    if latest.analysis is None:
        return []
    ms = next_evidence(latest.analysis.category, latest.analysis.facts or {}, (latest.published_at + timedelta(hours=5, minutes=30)).date())
    return [m for m in ms if not resolved_by(m["kind"], [x.analysis.facts or {} for x in ordered if x.analysis and x.published_at > latest.published_at],
                                             [x.analysis.category for x in ordered if x.analysis and x.published_at > latest.published_at])]


def _promise_links(db: Session, members: list[Announcement]) -> list[dict]:
    from app.services.promise import event_links

    out, seen = [], set()
    for m in members:
        for l in event_links(db, m):
            if l["id"] not in seen:
                seen.add(l["id"])
                out.append(l)
    return out


def _event_dna(members: list[Announcement]) -> list[dict]:
    """What each filing in an event contributed, oldest first. A filing that restates an earlier one is marked as a
    repeat so readers can tell a new approval or outcome from the same disclosure filed twice."""
    out, seen_texts = [], []
    for m in sorted(members, key=lambda x: x.published_at):
        ea = m.analysis
        f = ea.facts if ea else {}
        added = f.get("what_changed") or (ea.summary if ea else None) or m.title
        role = f.get("filing_role") or "Disclosure"
        from app.services.filing_facts import similar

        if any(similar(added, t) >= 0.85 for t in seen_texts) and role not in ("Regulatory approval", "Shareholder approval", "Completed", "Revised terms"):
            role = "Repeat disclosure"
        seen_texts.append(added)
        cite = next((c for c in (ea.citations if ea else []) if c.get("kind") == "filing"), None)
        out.append({"id": m.id, "published_at": _iso(m.published_at), "role": role, "added": added,
                    "title": f.get("subject") or m.title, "source_url": m.source_url, "citation": cite})
    return out


def _window_coverage(db: Session, companies: list[Company], start: datetime, end: datetime) -> dict:
    """Is the window actually covered? The filings listing returns only each company's latest filings, so a window that
    starts before a company's continuous-coverage boundary may hide filings FINN never saw."""
    from app.models import SourceCoverage

    rows = {r.company_id: r for r in db.scalars(select(SourceCoverage).where(SourceCoverage.source == "filings_listing")).all()}
    gaps, unknown, failing = [], [], []
    bounds = []
    for c in companies:
        r = rows.get(c.id)
        if r is None or r.last_success_at is None:
            unknown.append(c.ticker)
            continue
        if r.last_error and (r.last_attempt_at or utcnow()) > r.last_success_at:
            failing.append({"ticker": c.ticker, "last_success": _iso(r.last_success_at), "error": r.last_error[:160]})
        since = r.continuous_since
        if since is not None:
            bounds.append(since)
        if since is None or since > start:
            gaps.append({"ticker": c.ticker, "covered_from": _iso(since) if since else None,
                         "last_success": _iso(r.last_success_at), "items_in_last_listing": r.items_last})
    last_checked = min((r.last_success_at for cid, r in rows.items() if r.last_success_at and cid in {c.id for c in companies}),
                       default=None)
    complete = not gaps and not unknown and not failing
    return {
        "source": "Latest filings per company from the exchange-filings index (a limited window, not a full archive)",
        "limited_window": True, "complete_for_window": complete,
        "complete_from": _iso(max(bounds)) if bounds and not unknown else None,
        "checked_through": _iso(last_checked),
        "gaps": gaps, "never_checked": unknown, "failing": failing,
    }


def _collection_status(db: Session) -> dict:
    """Did the last refresh complete? Which sources failed? Lets the UI tell 'nothing material' from 'data missing'."""
    run = db.scalars(select(IngestionRun).where(IngestionRun.job == "daily").order_by(desc(IngestionRun.started_at)).limit(1)).first()
    if run is None:
        return {"status": "never", "message": "No refresh has run yet."}
    failed = sorted({e.get("ticker") for e in (run.errors or []) if e.get("ticker")})
    missing_prices = (run.stats or {}).get("market", {}).get("missing", [])
    status = run.status if run.finished_at else "running"
    return {"status": status, "finished_at": _iso(run.finished_at), "started_at": _iso(run.started_at),
            "filings_unavailable_for": failed, "prices_unavailable_for": missing_prices,
            "complete": status == "ok" and not failed and not missing_prices}


def _freshness(db: Session) -> dict:
    last_run = db.scalars(select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(1)).first()
    last_price = db.scalar(select(func.max(Price.trading_date)))
    last_ann = db.scalar(select(func.max(Announcement.ingested_at)))
    s = get_settings()
    return {
        "last_ingestion_run": _iso(last_run.finished_at or last_run.started_at) if last_run else None,
        "last_ingestion_status": last_run.status if last_run else None,
        "latest_trading_date": str(last_price) if last_price else None,
        "announcements_checked_at": _iso(last_ann),
        "sources": {"prices": "Yahoo Finance EOD (yfinance)", "filings": "BSE filings via Screener.in index",
                    "fundamentals": "Annual reports + concall transcripts (BSE / company IR PDFs)"},
        "ai_mode": f"llm ({s.llm_model_fast} / {s.llm_model_reasoning})" if s.llm_enabled else "deterministic (no LLM key)",
        "next_refresh": __import__("app.workers.scheduler", fromlist=["status"]).status().get("next_run"),
    }


# ------------------------------------------------------------------ portfolio


@router.get("/portfolio")
def get_portfolio(db: Session = Depends(get_db)):
    prefs = _prefs(db)
    since = utcnow() - timedelta(days=7)
    rows = []
    for c in pipeline.portfolio_companies(db):
        snap = market_svc.snapshot(db, c.id)
        # Count events, not filings: two filings about one reappointment are one development.
        n_imp = db.scalar(select(func.count(func.distinct(func.coalesce(Announcement.event_group, Announcement.content_hash))))
                          .select_from(Announcement)
                          .join(EventAnalysis, EventAnalysis.announcement_id == Announcement.id)
                          .where(Announcement.company_id == c.id, Announcement.published_at >= since,
                                 EventAnalysis.importance_level.in_(["high", "medium"])))
        ret = snap["return_pct"]["value"] if snap else None
        vr = snap["volume_ratio"]["value"] if snap else None
        rows.append({
            "ticker": c.ticker, "company_name": c.company_name, "sector": c.sector, "size": c.market_cap_category,
            "is_financial": c.is_financial, "market": snap,
            "flags": {"price_move": ret is not None and abs(ret) >= prefs["price_move_pct"],
                      "volume_spike": vr is not None and vr >= prefs["volume_multiple"]},
            "important_announcements_7d": n_imp or 0,
        })
    rets = [h["market"]["return_pct"]["value"] for h in rows if h["market"]]
    sessions = [h["market"]["trading_date"] for h in rows if h["market"]]
    valid = [r for r in rets if r is not None]
    summary = {
        "session_date": max(set(sessions), key=sessions.count) if sessions else None,
        "up": sum(r > 0 for r in valid), "down": sum(r < 0 for r in valid), "unchanged": sum(r == 0 for r in valid),
        "unavailable": len(rows) - len(valid),
        "equal_weight_pct": round(sum(valid) / len(valid), 2) if valid else None, "equal_weight_n": len(valid),
        "badge_counts": "events",  # recent-filing badges count grouped events, not individual filings
    }
    return {"holdings": rows, "thresholds": prefs, "freshness": _freshness(db), "summary": summary}


@router.post("/portfolio/holdings", status_code=201, dependencies=[Depends(rate_limit(10, 60))])
def add_holding(body: dict, db: Session = Depends(get_db)):
    """Add an NSE company to the watchlist; unseen tickers are resolved from the primary-document index first."""
    try:
        t = normalise_ticker(str(body.get("ticker", "")))
    except ValueError as e:
        raise HTTPException(422, str(e))
    company = db.scalars(select(Company).where(Company.ticker == t)).first()
    if company is None:
        try:
            company, _ = pipeline.upsert_company(db, t)
        except FetchError:
            raise HTTPException(404, f"No company found for NSE symbol '{t}'.")
    pf = db.scalars(select(Portfolio)).first()
    if db.scalars(select(PortfolioHolding).where(PortfolioHolding.portfolio_id == pf.id, PortfolioHolding.company_id == company.id)).first():
        raise HTTPException(409, f"{t} is already in your holdings.")
    db.add(PortfolioHolding(portfolio_id=pf.id, company_id=company.id))
    db.commit()
    pipeline.refresh_market(db, [company])
    return {"ticker": company.ticker, "company_name": company.company_name}


@router.delete("/portfolio/holdings/{ticker}", status_code=204)
def remove_holding(ticker: str, db: Session = Depends(get_db)):
    t = normalise_ticker(ticker)
    h = db.scalars(select(PortfolioHolding).join(Company, Company.id == PortfolioHolding.company_id).where(Company.ticker == t)).first()
    if h is None:
        raise HTTPException(404, f"{t} is not in your holdings.")
    db.delete(h)
    db.commit()


@router.get("/portfolio/summary")
def portfolio_summary(db: Session = Depends(get_db)):
    p = get_portfolio(db)
    rets = [h["market"]["return_pct"]["value"] for h in p["holdings"] if h["market"] and h["market"]["return_pct"]["value"] is not None]
    return {"holdings": len(p["holdings"]), "with_market_data": len(rets),
            "advancers": sum(r > 0 for r in rets), "decliners": sum(r < 0 for r in rets),
            "equal_weight_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
            "flags": sum(h["flags"]["price_move"] or h["flags"]["volume_spike"] for h in p["holdings"]),
            "freshness": p["freshness"]}


@router.get("/companies/{ticker}")
def get_company(company: Company = Depends(company_or_404), db: Session = Depends(get_db)):
    anns = db.scalars(select(Announcement).where(Announcement.company_id == company.id)
                      .order_by(desc(Announcement.published_at)).limit(15)).all()
    in_pf = db.scalar(select(func.count()).select_from(PortfolioHolding).where(PortfolioHolding.company_id == company.id))
    return {
        # The document index's "About" blurb is third-party text, so it is not returned for display; Research shows a
        # profile quoted from the company's own annual report instead (GET /companies/{t}/profile).
        "ticker": company.ticker, "company_name": company.company_name, "sector": company.sector,
        "is_financial": company.is_financial, "bse_code": company.bse_code, "in_portfolio": bool(in_pf),
        "market": market_svc.snapshot(db, company.id), "latest_results": results_snapshot(db, company),
        "announcements": [_card(a, db).model_dump() for a in anns if a.analysis],
    }


@router.post("/companies/{ticker}/index", dependencies=[Depends(rate_limit(6, 60))])
def index_company(ticker: str, db: Session = Depends(get_db)):
    """Resolve any NSE ticker: fetch its document index + filed results, then index recent primary docs."""
    try:
        t = normalise_ticker(ticker)
        company, _ = pipeline.upsert_company(db, t)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except FetchError as e:
        raise HTTPException(404, f"Could not resolve {ticker}: {e}")
    pipeline.refresh_market(db, [company])
    pipeline.process_pending(db, limit=10)
    corpus = pipeline.ensure_research_corpus(db, company)
    return {"ticker": company.ticker, "company_name": company.company_name, "corpus": corpus}


def _index_annual_report(company_id: int) -> None:
    from app.services.profile import latest_annual_report

    db = SessionLocal()
    try:
        ar = latest_annual_report(db, company_id)
        if ar is not None and ar.status != "indexed":
            pipeline.index_document(db, ar)
    finally:
        db.close()


@router.get("/companies/{ticker}/profile")
def company_profile(background: BackgroundTasks, company: Company = Depends(company_or_404), db: Session = Depends(get_db)):
    """What the company does, quoted from its latest annual report with page citations (primary source only)."""
    from app.services.profile import business_profile

    p = business_profile(db, company)
    if p["status"] == "not_indexed":
        background.add_task(_index_annual_report, company.id)
        return {**p, "status": "indexing"}
    return p


@router.get("/companies/{ticker}/financials")
def financials(company: Company = Depends(company_or_404), db: Session = Depends(get_db)):
    q, meta = load_series(db, company.id, "quarter")
    keys = ["revenue", "operating_profit", "opm_pct", "pbt", "pat", "nii", "pat_margin_pct", "gnpa_pct"]
    periods = sorted({p for k in keys for p in q.get(k, {})}, key=lambda p: (int(p[4:]), int(p[1])))
    return {
        "ticker": company.ticker, "is_financial": company.is_financial, "basis": meta.get("basis"),
        "source": meta.get("source"), "source_url": meta.get("source_url"), "fetched_at": meta.get("fetched_at"),
        "quarterly": [{"period": p, **{k: q.get(k, {}).get(p) for k in keys}} for p in periods],
        "annual": annual_overview(db, company), "latest_results": results_snapshot(db, company),
    }


@router.get("/companies/{ticker}/documents")
def documents(company: Company = Depends(company_or_404), db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).where(Document.company_id == company.id)
                      .order_by(desc(Document.publication_date))).all()
    return [{"id": d.id, "title": d.title, "document_type": d.document_type, "reporting_period": d.reporting_period,
             "publication_date": d.publication_date.isoformat() if d.publication_date else None, "source_url": d.source_url,
             "status": d.status, "pages": d.page_count, "error": d.error} for d in docs]


@router.get("/companies/{ticker}/guidance")
def guidance(company: Company = Depends(company_or_404), db: Session = Depends(get_db)):
    rows = db.scalars(select(ManagementGuidance).where(ManagementGuidance.company_id == company.id)).all()
    return [{"id": g.id, "metric": g.metric, "type": g.guidance_type, "target_min": g.target_min, "target_max": g.target_max,
             "target_value": g.target_value, "comparator": g.comparator, "target_period": g.target_period,
             "horizon": g.horizon, "basis": g.basis, "statement": g.statement, "speaker": g.speaker,
             "guidance_date": g.guidance_date.isoformat() if g.guidance_date else None, "source_page": g.source_page,
             "document_id": g.source_document_id, "extractor": g.extractor} for g in rows]


# ------------------------------------------------------------------ review workflow ("Since your last review")


def _review_state(db: Session) -> dict:
    p = db.scalars(select(UserPreference).where(UserPreference.key == "review_state")).first()
    return dict(p.value) if p else {}


def _set_review_state(db: Session, **kw) -> dict:
    p = db.scalars(select(UserPreference).where(UserPreference.key == "review_state")).first()
    if p is None:
        p = UserPreference(key="review_state", value={})
        db.add(p)
        db.flush()   # sessions don't autoflush: a second call in the same request must find this row
    v = dict(p.value or {})
    v.update(kw)
    p.value = v
    return v


@router.get("/review")
def review(days: int = Query(14, ge=1, le=60), db: Session = Depends(get_db)):
    """New events, meaningful updates to events you reviewed, and unresolved developments — kept apart. Nothing is marked
    as understood by viewing this; only explicit review actions change state."""
    from app.models import EventReview

    state = _review_state(db)
    since = utcnow() - timedelta(days=days)
    anns = [a for a in db.scalars(select(Announcement).join(PortfolioHolding, PortfolioHolding.company_id == Announcement.company_id)
                                  .where(Announcement.published_at >= since, Announcement.processing_status == "processed")).all() if a.analysis]
    groups: dict[str, list[Announcement]] = {}
    for a in anns:
        groups.setdefault(a.event_group or f"solo{a.id}", []).append(a)
    reviews = {r.event_key: r for r in db.scalars(select(EventReview)).all()}
    new, updated, unresolved, done = [], [], [], 0
    for key, members in groups.items():
        members.sort(key=lambda x: x.published_at, reverse=True)
        card = _card(members[0], db, members[1:])
        r = reviews.get(key)
        prominent = card.importance_level in ("high", "medium")
        if r is None:
            if prominent:
                card.review = {"status": "new"}
                new.append(card)
            continue
        unseen = [m for m in members if m.id not in (r.filings_seen or [])]
        if unseen:   # a repeat of a disclosure already reviewed is not a change (same rule as Investor Handover)
            from app.services.monitoring import REPEAT_ROLES

            roles = {d["id"]: d["role"] for d in _event_dna(members)}
            if all(roles.get(m.id) in REPEAT_ROLES for m in unseen):
                unseen = []
        card.review = {"status": r.status, "reviewed_at": _iso(r.reviewed_at), "watch": r.watch,
                       "new_filings": [d for d in card.supporting if d["id"] in {m.id for m in unseen}] if unseen else []}
        if unseen and not card.review["new_filings"]:
            card.review["new_filings"] = [{"id": m.id, "published_at": _iso(m.published_at), "role": (m.analysis.facts or {}).get("filing_role"),
                                           "added": (m.analysis.facts or {}).get("what_changed") or m.analysis.headline} for m in unseen]
        watch_open = r.status == "watching" and r.watch and any(m["kind"] == r.watch.get("kind") for m in card.milestones)
        if unseen:
            card.review["update"] = ("Milestone reached" if r.status == "watching" and r.watch and not watch_open else "New filing since your review")
            updated.append(card)
        elif watch_open:
            unresolved.append(card)
        else:
            done += 1
    rank = lambda c: (c.importance_score, c.published_at)  # noqa: E731
    return {
        "first_visit": not state.get("started_at"), "started_at": state.get("started_at"), "last_review_at": state.get("last_review_at"),
        "window_days": days, "new": sorted(new, key=rank, reverse=True), "updated": sorted(updated, key=rank, reverse=True),
        "unresolved": sorted(unresolved, key=rank, reverse=True), "reviewed_without_changes": done,
        "note": "Marking an event reviewed records which of its filings you have seen; a later filing brings it back as an update.",
    }


@router.post("/review/start")
def review_start(db: Session = Depends(get_db)):
    """First visit: start tracking without marking anything as reviewed."""
    st = _review_state(db)
    if not st.get("started_at"):
        st = _set_review_state(db, started_at=_iso(utcnow()))
        db.commit()
    return st


@router.post("/review/{event_key}")
def review_action(event_key: str, body: dict, db: Session = Depends(get_db)):
    from app.models import EventReview

    action = body.get("action")
    if action not in ("reviewed", "watch", "unwatch", "reopen"):
        raise HTTPException(422, "action must be reviewed, watch, unwatch or reopen")
    members = db.scalars(select(Announcement).where(Announcement.event_group == event_key)).all()
    if not members and event_key.startswith("solo") and event_key[4:].isdigit():
        a = db.get(Announcement, int(event_key[4:]))
        members = [a] if a else []
    if not members:
        raise HTTPException(404, "Event not found")
    r = db.scalars(select(EventReview).where(EventReview.event_key == event_key)).first()
    if action == "reopen":
        if r is not None:
            db.delete(r)
        db.commit()
        return {"event_key": event_key, "status": "new"}
    if r is None:
        from sqlalchemy.exc import IntegrityError

        try:
            with db.begin_nested():
                r = EventReview(event_key=event_key, company_id=members[0].company_id, filings_seen=[], status="reviewed")
                db.add(r)
        except IntegrityError:   # a concurrent request created the review first; merge into it
            r = db.scalars(select(EventReview).where(EventReview.event_key == event_key)).first()
    now = utcnow()
    # Record exactly the filings the investor was shown (the item's version). A filing that arrived after the page was
    # loaded stays unseen, so it still appears as an update instead of being silently marked reviewed.
    shown = body.get("filings")
    member_ids = {m.id for m in members}
    seen_now = member_ids if shown is None else member_ids & {int(x) for x in shown}
    r.filings_seen = sorted(set(r.filings_seen or []) | seen_now)
    r.reviewed_at = r.updated_at = now
    if action == "watch":
        ms = _milestones(members)
        m = next((x for x in ms if x["kind"] == body.get("milestone")), ms[0] if ms else None)
        if m is None:
            raise HTTPException(422, "No supported milestone to watch for this event")
        r.status, r.watch = "watching", {**m, "since": _iso(now), "is_alert": False}
    elif action == "unwatch":
        r.status, r.watch = "reviewed", None
    else:
        r.status = "watching" if r.watch else "reviewed"
    st = _review_state(db)
    _set_review_state(db, last_review_at=_iso(now), **({} if st.get("started_at") else {"started_at": _iso(now)}))
    db.commit()
    return {"event_key": event_key, "status": r.status, "watch": r.watch, "filings_seen": r.filings_seen}


@router.get("/companies/{ticker}/coverage")
def document_coverage(company: Company = Depends(company_or_404), db: Session = Depends(get_db)):
    """Listing, download, extraction and analysis coverage reported separately, with failures and pending retries."""
    from app.models import IngestionJob, SourceCoverage
    from app.services import jobs as jobsvc

    docs = db.scalars(select(Document).where(Document.company_id == company.id)).all()
    jobmap = {j.document_id: j for j in db.scalars(select(IngestionJob).where(IngestionJob.company_id == company.id)).all()}
    by_type: dict = {}
    for d in docs:
        t = by_type.setdefault(d.document_type, {"listed": 0, "downloaded": 0, "extracted": 0, "indexed": 0, "analysed": 0,
                                                 "failed": 0, "retrying": 0, "not_attempted": 0})
        j = jobmap.get(d.id)
        t["listed"] += 1
        t["downloaded"] += bool(d.storage_path)
        t["extracted"] += bool(d.page_count)
        t["indexed"] += d.status == "indexed"
        if d.document_type == "concall_transcript":
            t["analysed"] += bool(j and j.status == "succeeded" and "guidance_items" in (j.result or {}))
        else:
            t["analysed"] += d.status == "indexed"
        t["failed"] += d.status == "failed"
        t["retrying"] += d.status == "retrying"
        t["not_attempted"] += d.status == "discovered" and j is None
    lc = db.scalars(select(SourceCoverage).where(SourceCoverage.company_id == company.id,
                                                 SourceCoverage.source == "filings_listing")).first()
    return {
        "ticker": company.ticker, "documents": by_type,
        "notes": {"listed": "found in the document index", "downloaded": "PDF fetched and stored",
                  "extracted": "text layer read", "indexed": "chunked and searchable",
                  "analysed": "transcripts: guidance extracted for the Promise Ledger; others: indexed"},
        "failures": [{"document_id": d.id, "title": d.title, "error_class": jobmap[d.id].error_class if d.id in jobmap else None,
                      "explanation": d.error} for d in docs if d.status == "failed"][:20],
        "retrying": [jobsvc.as_dict(j) for j in jobmap.values() if j.status in ("retry", "running")][:20],
        "filings_listing": None if lc is None else {
            "last_success": _iso(lc.last_success_at), "last_error": lc.last_error, "items_last": lc.items_last,
            "oldest_item": _iso(lc.oldest_item_at), "newest_item": _iso(lc.newest_item_at),
            "continuous_since": _iso(lc.continuous_since), "limited_window": lc.limited_window},
        "jobs": jobsvc.summary(db, company.id),
    }


@router.get("/companies/{ticker}/financials/history")
def financials_history(metric: str | None = None, period: str | None = None, company: Company = Depends(company_or_404),
                       db: Session = Depends(get_db)):
    from app.services import financial_history as fh

    return {"ticker": company.ticker, "snapshots": fh.snapshots(db, company.id),
            "observations": fh.history(db, company.id, metric, period)[:500]}


@router.get("/companies/{ticker}/promise-tracker")
def promises(company: Company = Depends(company_or_404), db: Session = Depends(get_db)):
    out = promise_tracker(db, company)
    # How much the ledger could read: an empty ledger means "no guidance found" only if transcripts were actually read.
    tx = db.scalars(select(Document).where(Document.company_id == company.id, Document.document_type == "concall_transcript")).all()
    out["coverage"] = {"transcripts_indexed": sum(d.status == "indexed" for d in tx), "transcripts_listed": len(tx),
                       "periods": sorted({d.reporting_period for d in tx if d.status == "indexed" and d.reporting_period})}
    return out


# ------------------------------------------------------------------ signals / briefing


@router.get("/signals", response_model=Briefing)
def signals(hours: int = Query(24, ge=1, le=24 * 90), since: datetime | None = None, until: datetime | None = None,
            ticker: str | None = None, db: Session = Depends(get_db)):
    from app.services.periods import to_utc_naive

    if since is not None and since.tzinfo is not None:
        since = to_utc_naive(since)
    if until is not None and until.tzinfo is not None:
        until = to_utc_naive(until)
    end = until or utcnow()
    start = since or end - timedelta(hours=hours)
    if start >= end:
        raise HTTPException(422, "The start of the window must be before its end.")
    q = select(Announcement).where(Announcement.published_at >= start, Announcement.published_at <= end,
                                   Announcement.processing_status == "processed")
    if ticker:   # any covered company (Research shows an unfamiliar company's latest developments too)
        q = q.join(Company, Company.id == Announcement.company_id).where(Company.ticker == normalise_ticker(ticker))
    else:
        q = q.join(PortfolioHolding, PortfolioHolding.company_id == Announcement.company_id)
    anns = [a for a in db.scalars(q).all() if a.analysis]
    groups: dict[str, list[Announcement]] = {}
    for a in anns:
        groups.setdefault(a.event_group or f"solo{a.id}", []).append(a)
    cards = []
    for members in groups.values():
        members.sort(key=lambda a: a.published_at, reverse=True)  # latest filing = current state of the event
        cards.append(_card(members[0], db, members[1:]))
    cards.sort(key=lambda c: (c.importance_score, c.published_at), reverse=True)
    prominent = [c for c in cards if c.importance_level in ("high", "medium")]
    routine = [c for c in cards if c.importance_level in ("low", "routine")]

    prefs = _prefs(db)
    anomalies = []
    for c in pipeline.portfolio_companies(db):
        s = market_svc.snapshot(db, c.id)
        if not s:
            continue
        r, v = s["return_pct"]["value"], s["volume_ratio"]["value"]
        reasons = []
        if r is not None and abs(r) >= prefs["price_move_pct"]:
            reasons.append(f"{r:+.1f}% day move (threshold ±{prefs['price_move_pct']:g}%)")
        if v is not None and v >= prefs["volume_multiple"]:
            reasons.append(f"volume {v:.1f}× 20-day avg (threshold {prefs['volume_multiple']:g}×)")
        if reasons:
            anomalies.append({"ticker": c.ticker, "company_name": c.company_name, "trading_date": s["trading_date"],
                              "close": s["close"], "return_pct": r, "volume_ratio": v, "reasons": reasons})
    anomalies.sort(key=lambda x: abs(x["return_pct"] or 0), reverse=True)
    recent_alerts = db.scalars(select(Alert).where(Alert.status == "new").order_by(desc(Alert.created_at)).limit(5)).all()
    holdings = pipeline.portfolio_companies(db)
    return Briefing(
        window={"start": _iso(start), "end": _iso(end), "hours": round((end - start).total_seconds() / 3600, 1),
                "custom": since is not None or until is not None},
        generated_at=_iso(utcnow()), freshness=_freshness(db),
        coverage={"holdings": len(holdings), "companies_with_filings": len({a.company_id for a in anns}),
                  "filings": len(anns), "filings_source": _window_coverage(db, holdings, start, end)},
        collection=_collection_status(db),
        counts={"total": len(anns), "events": len(cards), "high": sum(c.importance_level == "high" for c in cards),
                "medium": sum(c.importance_level == "medium" for c in cards), "routine": len(routine),
                "anomalies": len(anomalies)},
        signals=prominent, routine=routine, anomalies=anomalies,
        alerts=[{"id": a.id, "ticker": a.company.ticker, "message": a.message, "type": a.alert_type,
                 "created_at": _iso(a.created_at), "related_event_id": a.related_event_id} for a in recent_alerts],
    )


@router.get("/signals/{signal_id}", response_model=SignalCard)
def signal(signal_id: int, db: Session = Depends(get_db)):
    a = db.get(Announcement, signal_id)
    if not a or not a.analysis:
        raise HTTPException(404, "Signal not found")
    rel = db.scalars(select(Announcement).where(Announcement.event_group == a.event_group, Announcement.id != a.id)
                     .order_by(desc(Announcement.published_at))).all() if a.event_group else []
    return _card(a, db, list(rel))


@router.get("/announcements")
def announcements(ticker: str | None = None, limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    q = select(Announcement).order_by(desc(Announcement.published_at)).limit(limit)
    if ticker:
        q = q.join(Company, Company.id == Announcement.company_id).where(Company.ticker == normalise_ticker(ticker))
    return [{"id": a.id, "ticker": a.company.ticker, "title": a.title, "summary": a.exchange_summary,
             "category": a.category, "published_at": _iso(a.published_at), "ingested_at": _iso(a.ingested_at),
             "source": a.source, "source_url": a.source_url, "status": a.processing_status, "is_sample": a.is_sample}
            for a in db.scalars(q).all()]


@router.get("/market/anomalies")
def anomalies(db: Session = Depends(get_db)):
    return signals(hours=24, since=None, until=None, ticker=None, db=db).anomalies


# ------------------------------------------------------------------ research + evidence


@router.post("/research", response_model=ResearchResponse, dependencies=[Depends(rate_limit(15, 60))])
def post_research(req: ResearchRequest, db: Session = Depends(get_db)):
    try:
        t = normalise_ticker(req.ticker)
    except ValueError as e:
        raise HTTPException(422, str(e))
    company = db.scalars(select(Company).where(Company.ticker == t)).first()
    if company is None:  # unseen ticker: resolve it from the document index first
        try:
            company, _ = pipeline.upsert_company(db, t)
        except FetchError:
            raise HTTPException(404, f"No primary-source document index found for '{t}'. Check the NSE symbol.")
    return research(db, company, req.question, filing_id=req.filing_id,
                    history=[m.model_dump() for m in req.history] or None)


@router.get("/ai/status")
def ai_status():
    s = get_settings()
    return {"enabled": s.llm_enabled, "fast_model": s.llm_model_fast if s.llm_enabled else None,
            "reasoning_model": s.llm_model_reasoning if s.llm_enabled else None,
            "provider": (s.llm_base_url or "https://api.openai.com/v1") if s.llm_enabled else None,
            "max_calls_per_run": s.llm_max_calls_per_run,
            "fallback_models": {"reasoning": s.llm_fallback_models_reasoning, "fast": s.llm_fallback_models_fast},
            "cooling_down_seconds": __import__("app.llm.client", fromlist=["LLM"]).LLM.status() if s.llm_enabled else {}}


@router.post("/assistant/alerts", dependencies=[Depends(rate_limit(20, 60))])
def alerts_assistant(req: AssistantRequest, db: Session = Depends(get_db)):
    from app.agents.alerts_agent import assist

    return assist(db, req.message, [m.model_dump() for m in req.history])


@router.post("/assistant/alerts/apply")
def alerts_assistant_apply(req: ApplyActionsRequest, db: Session = Depends(get_db)):
    from pydantic import ValidationError

    from app.services import alert_ops

    try:
        actions = alert_ops.ActionList.validate_python([{k: v for k, v in a.items() if k != "description"} for a in req.actions])
    except ValidationError as e:
        raise HTTPException(422, str(e)[:500])
    return {"results": [alert_ops.apply(db, a) for a in actions]}


@router.get("/evidence/chunks/{chunk_id}")
def evidence_chunk(chunk_id: int, db: Session = Depends(get_db)):
    c = db.get(DocumentChunk, chunk_id)
    if not c:
        raise HTTPException(404, "Evidence not found")
    d = c.document
    neighbours = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == d.id,
                                                        DocumentChunk.chunk_index.between(c.chunk_index - 1, c.chunk_index + 1))
                            .order_by(DocumentChunk.chunk_index)).all()
    prev_id = db.scalar(select(DocumentChunk.id).where(DocumentChunk.document_id == d.id, DocumentChunk.chunk_index < c.chunk_index)
                        .order_by(desc(DocumentChunk.chunk_index)).limit(1))
    next_id = db.scalar(select(DocumentChunk.id).where(DocumentChunk.document_id == d.id, DocumentChunk.chunk_index > c.chunk_index)
                        .order_by(DocumentChunk.chunk_index).limit(1))
    return {
        "chunk_id": c.id, "page_number": c.page_number, "speaker": c.speaker, "speaker_role": c.speaker_role,
        "section": c.section, "content": c.content, "prev_chunk_id": prev_id, "next_chunk_id": next_id,
        "context": [{"chunk_id": n.id, "page_number": n.page_number, "speaker": n.speaker, "content": n.content} for n in neighbours],
        "document": {"id": d.id, "title": d.title, "document_type": d.document_type, "ticker": d.company.ticker,
                     "reporting_period": d.reporting_period, "source_url": d.source_url,
                     "publication_date": d.publication_date.isoformat() if d.publication_date else None,
                     "pages": d.page_count, "has_local_copy": bool(d.storage_path)},
    }


@router.get("/documents/{doc_id}/pages/{page}")
def document_page(doc_id: int, page: int, db: Session = Depends(get_db)):
    """All extracted text on one page of a primary document, in reading order (for the Evidence Lens page view)."""
    d = db.get(Document, doc_id)
    if not d or d.status != "indexed":
        raise HTTPException(404, "Document not indexed")
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc_id, DocumentChunk.page_number == page)
                        .order_by(DocumentChunk.chunk_index)).all()
    pages = sorted({p for (p,) in db.execute(select(DocumentChunk.page_number).where(DocumentChunk.document_id == doc_id).distinct())})
    prev_p = max((p for p in pages if p < page), default=None)
    next_p = min((p for p in pages if p > page), default=None)
    return {"document_id": d.id, "title": d.title, "document_type": d.document_type, "reporting_period": d.reporting_period,
            "page": page, "pages_with_text": len(pages), "page_count": d.page_count, "prev_page": prev_p, "next_page": next_p,
            "passages": [{"chunk_id": c.id, "speaker": c.speaker, "speaker_role": c.speaker_role, "content": c.content} for c in chunks]}


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d or not d.storage_path:
        raise HTTPException(404, "No cached copy; open the original source_url")
    p = Path(d.storage_path).resolve()
    root = (get_settings().data_dir / "docs").resolve()
    if root not in p.parents or not p.exists():  # never serve files outside the document store
        raise HTTPException(404, "File not available")
    return FileResponse(p, media_type="application/pdf", headers={"Content-Disposition": "inline"})


# ------------------------------------------------------------------ alerts


def db_of(obj) -> Session:
    from sqlalchemy.orm import object_session

    return object_session(obj)


def _holding_count(db: Session) -> int:
    from app.ingestion.pipeline import portfolio_companies

    return len(portfolio_companies(db))


def _rule_out(r: AlertRule, log: dict | None = None) -> dict:
    spec = alert_svc.AlertRuleSpec.model_validate(r.parameters)
    return {"id": r.id, "name": r.name, "enabled": r.enabled, "rule_type": r.rule_type, "spec": r.parameters,
            "description": alert_svc.describe(spec), "created_from_text": r.created_from_text, "created_at": _iso(r.created_at),
            "ticker": r.company.ticker if r.company else None,
            "subject": r.company.company_name if r.company else "Any holding",
            "clauses": alert_svc.clauses_human(spec), "joiner": spec.logical_operator.lower(),
            "sentence": alert_svc.describe_human(spec, r.company.company_name if r.company else None),
            "conditions_editable": [{"index": i, "metric": c.metric, "operator": c.operator, "value": c.value}
                                    for i, c in enumerate(spec.conditions) if c.metric in alert_svc.MARKET_METRICS],
            "preview": alert_svc.preview(spec, r.company.company_name if r.company else None, _holding_count(db_of(r))),
            "last_evaluation": (log or {}).get("rules", {}).get(str(r.id))}


@router.get("/alerts")
def list_alerts(status: str | None = None, limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    q = select(Alert).order_by(desc(Alert.created_at)).limit(limit)
    if status:
        q = q.where(Alert.status == status)
    return [{"id": a.id, "ticker": a.company.ticker, "company_name": a.company.company_name, "type": a.alert_type,
             "message": a.message, "details": a.details,
             "as_of": a.as_of.isoformat() if a.as_of else None, "status": a.status, "created_at": _iso(a.created_at),
             "rule_id": a.rule_id, "related_event_id": a.related_event_id} for a in db.scalars(q).all()]


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertStatusUpdate, db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    a.status = body.status
    db.commit()
    return {"id": a.id, "status": a.status}


@router.get("/alert-rules")
def list_rules(db: Session = Depends(get_db)):
    log = alert_svc.evaluation_log(db)
    return [_rule_out(r, log) for r in db.scalars(select(AlertRule).order_by(AlertRule.id)).all()]


@router.get("/alert-rules/status")
def rules_status(db: Session = Depends(get_db)):
    return {"last_run": alert_svc.evaluation_log(db).get("last_run"),
            "latest_eod_session": str(db.scalar(select(func.max(Price.trading_date))) or "")}


@router.post("/alert-rules/evaluate", dependencies=[Depends(rate_limit(10, 60))])
def evaluate_rules_now(db: Session = Depends(get_db)):
    """Run every enabled rule now against stored EOD data and analysed filings (deterministic, no AI)."""
    fired = alert_svc.evaluate_all(db)
    return {"new_alerts": fired, **rules_status(db)}


@router.post("/alert-rules/parse", dependencies=[Depends(rate_limit(30, 60))])
def parse_rule(body: AlertRuleParseRequest, db: Session = Depends(get_db)):
    companies = list(db.scalars(select(Company)).all())
    res = alert_svc.parse_rule_text(body.text, companies)
    return res.model_dump()


@router.post("/alert-rules", status_code=201)
def create_rule(body: AlertRuleCreate, db: Session = Depends(get_db)):
    from app.services import alert_ops

    try:
        r, fired = alert_ops.create_rule(db, body.spec, body.name, body.created_from_text)
    except alert_ops.OpError as e:
        raise HTTPException(409 if "identical" in str(e) else 422, str(e))
    return {**_rule_out(r, alert_svc.evaluation_log(db)), "fired_now": fired}


@router.patch("/alert-rules/{rule_id}")
def update_rule(rule_id: int, body: AlertRuleUpdate, db: Session = Depends(get_db)):
    r = db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    if body.name is not None:
        r.name = body.name
    spec_changed = body.spec is not None
    if body.spec is not None:
        p = body.spec.model_dump()
        p["enabled"] = r.enabled
        r.parameters, r.rule_type = p, body.spec.rule_type
    if body.enabled is not None:
        r.enabled = body.enabled
        p = dict(r.parameters)
        p["enabled"] = body.enabled
        r.parameters = p
    db.commit()
    if spec_changed and r.enabled:
        # New thresholds: re-check against the latest close now, so the card's status reflects the edited rule.
        from app.ingestion.pipeline import portfolio_companies

        alert_svc.evaluate_rule(db, r, portfolio_companies(db) + ([r.company] if r.company else []))
        db.commit()
    return _rule_out(r, alert_svc.evaluation_log(db))


@router.delete("/alert-rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    db.delete(r)
    db.commit()


# ------------------------------------------------------------------ preferences + status


@router.get("/preferences")
def get_prefs(db: Session = Depends(get_db)):
    return {**_prefs(db), "categories": [{"value": c.value, "label": CATEGORY_LABELS[c]} for c in Category]}


@router.put("/preferences")
def put_prefs(body: PreferencesUpdate, db: Session = Depends(get_db)):
    cur = _prefs(db)

    def upsert(key, value):
        row = db.scalars(select(UserPreference).where(UserPreference.key == key)).first()
        if row:
            row.value = value
        else:
            db.add(UserPreference(key=key, value=value))

    th = {"price_move_pct": body.price_move_pct or cur["price_move_pct"],
          "volume_multiple": body.volume_multiple or cur["volume_multiple"]}
    upsert("thresholds", th)
    if body.watched_categories is not None:
        valid = {c.value for c in Category}
        bad = [c for c in body.watched_categories if c not in valid]
        if bad:
            raise HTTPException(422, f"Unknown categories: {bad}")
        upsert("watched_categories", {"categories": body.watched_categories})
    db.commit()
    return get_prefs(db)


@router.get("/status")
def status(db: Session = Depends(get_db)):
    runs = db.scalars(select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(5)).all()
    agg = db.execute(select(AgentRun.workflow, func.count(), func.sum(AgentRun.tokens_in + AgentRun.tokens_out),
                            func.sum(AgentRun.estimated_cost_usd), func.avg(AgentRun.duration_ms))
                     .group_by(AgentRun.workflow)).all()
    from app.workers import scheduler

    return {
        "freshness": _freshness(db),
        "scheduler": scheduler.status(),
        "runs": [{"job": r.job, "status": r.status, "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
                  "stats": r.stats, "errors": r.errors[:5]} for r in runs],
        "agent_usage": [{"workflow": w, "runs": n, "tokens": int(t or 0), "cost_usd": round(c or 0, 4),
                         "avg_ms": int(ms or 0)} for w, n, t, c, ms in agg],
        "counts": {"companies": db.scalar(select(func.count()).select_from(Company)),
                   "announcements": db.scalar(select(func.count()).select_from(Announcement)),
                   "documents_indexed": db.scalar(select(func.count()).select_from(Document).where(Document.status == "indexed")),
                   "chunks": db.scalar(select(func.count()).select_from(DocumentChunk)),
                   "guidance": db.scalar(select(func.count()).select_from(ManagementGuidance))},
    }


# ------------------------------------------------------------------ admin



@router.post("/admin/ingest/daily", dependencies=[Depends(require_admin)], status_code=202)
def admin_daily(background: BackgroundTasks):
    """Run the full daily refresh now (prices, filings, analysis, new documents, alerts) in the background."""
    from app.workers import scheduler

    if scheduler.status()["running"]:
        return {"status": "already_running"}
    background.add_task(scheduler.run_now, "manual (Refresh now)")
    return {"status": "started"}


@router.post("/admin/reanalyse", dependencies=[Depends(require_admin)])
def admin_reanalyse(db: Session = Depends(get_db)):
    """Re-run the filing workflow over stored filings (after a prompt/rule version bump)."""
    for a in db.scalars(select(Announcement)).all():
        a.processing_status = "pending"
    db.commit()
    return {"analysed": pipeline.process_pending(db, limit=1000)}
