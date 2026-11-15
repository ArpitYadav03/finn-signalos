"""SQLAlchemy ORM models. Portable across SQLite (local) and PostgreSQL (Supabase)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # NSE symbol, e.g. TCS
    company_name: Mapped[str] = mapped_column(String(200))
    exchange: Mapped[str] = mapped_column(String(8), default="NSE")
    sector: Mapped[str | None] = mapped_column(String(80))
    market_cap_category: Mapped[str | None] = mapped_column(String(16))  # large / mid / small
    is_financial: Mapped[bool] = mapped_column(Boolean, default=False)  # banks/NBFCs: no EBITDA
    bse_code: Mapped[str | None] = mapped_column(String(16))
    about: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"
    __table_args__ = (UniqueConstraint("portfolio_id", "company_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    company: Mapped[Company] = relationship()


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("company_id", "trading_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    trading_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Announcement(Base):
    __tablename__ = "announcements"
    __table_args__ = (UniqueConstraint("company_id", "content_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    exchange_summary: Mapped[str | None] = mapped_column(Text)  # one-liner shown by the source listing
    source: Mapped[str] = mapped_column(String(40))  # e.g. "screener:bse", "fixture"
    source_url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # IST-naive converted to UTC-naive
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))
    event_group: Mapped[str | None] = mapped_column(String(64), index=True)  # groups the same corporate event
    category: Mapped[str | None] = mapped_column(String(32))
    processing_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/processed/failed
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    company: Mapped[Company] = relationship()
    analysis: Mapped["EventAnalysis | None"] = relationship(back_populates="announcement", uselist=False)


class EventAnalysis(Base):
    __tablename__ = "event_analysis"
    id: Mapped[int] = mapped_column(primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), unique=True
    )
    category: Mapped[str] = mapped_column(String(32))
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    headline: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    why_it_matters: Mapped[str | None] = mapped_column(Text)
    importance_score: Mapped[float] = mapped_column(Float)
    importance_level: Mapped[str] = mapped_column(String(12))  # high / medium / low / routine
    reasoning: Mapped[list] = mapped_column(JSON, default=list)  # list of score components
    key_metrics: Mapped[list] = mapped_column(JSON, default=list)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    model_name: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    announcement: Mapped[Announcement] = relationship(back_populates="analysis")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("company_id", "source_url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    document_type: Mapped[str] = mapped_column(String(32))  # annual_report / concall_transcript / filing / presentation
    title: Mapped[str] = mapped_column(Text)
    reporting_period: Mapped[str | None] = mapped_column(String(32))  # e.g. FY26, Q2FY27
    source_url: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="discovered")  # discovered/indexed/failed/skipped
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    company: Mapped[Company] = relationship()


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(200))
    speaker: Mapped[str | None] = mapped_column(String(120))
    speaker_role: Mapped[str | None] = mapped_column(String(24))  # management / analyst / moderator
    content: Mapped[str] = mapped_column(Text)
    # Embedding stored as JSON for portability; pgvector column is the documented upgrade path (ADR-003).
    embedding: Mapped[list | None] = mapped_column(JSON)
    document: Mapped[Document] = relationship()


class FinancialMetric(Base):
    __tablename__ = "financial_metrics"
    __table_args__ = (
        UniqueConstraint("company_id", "fiscal_period", "period_type", "statement_basis", "metric_name"),
        Index("ix_fm_company_metric", "company_id", "metric_name"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    fiscal_period: Mapped[str] = mapped_column(String(16))  # FY26 or Q2FY27
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(8))  # quarter / annual
    statement_basis: Mapped[str] = mapped_column(String(16))  # consolidated / standalone
    metric_name: Mapped[str] = mapped_column(String(48))
    metric_value: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    unit: Mapped[str] = mapped_column(String(12), default="crore")  # crore / pct / days / rs
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    source_page: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ManagementGuidance(Base):
    __tablename__ = "management_guidance"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(48))  # revenue_growth / ebitda_margin / ebit_margin / capex / ...
    guidance_type: Mapped[str] = mapped_column(String(16))  # quantitative / qualitative
    target_value: Mapped[float | None] = mapped_column(Float)
    target_min: Mapped[float | None] = mapped_column(Float)
    target_max: Mapped[float | None] = mapped_column(Float)
    comparator: Mapped[str | None] = mapped_column(String(8))  # ">=", "<=", "range", "="
    unit: Mapped[str | None] = mapped_column(String(12))
    basis: Mapped[str | None] = mapped_column(String(32))  # reported_inr / constant_currency / unknown
    horizon: Mapped[str | None] = mapped_column(String(16))  # long_term / period
    target_period: Mapped[str | None] = mapped_column(String(16))  # FY27, Q4FY27
    guidance_date: Mapped[date | None] = mapped_column(Date)
    statement: Mapped[str] = mapped_column(Text)  # verbatim quote
    speaker: Mapped[str | None] = mapped_column(String(120))
    conditions: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    source_page: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="SET NULL"))
    extractor: Mapped[str] = mapped_column(String(48))  # rules-v1 / llm:<model>
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / revised / superseded
    document: Mapped[Document] = relationship()


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))  # None = portfolio
    rule_type: Mapped[str] = mapped_column(String(24))  # market / announcement
    parameters: Mapped[dict] = mapped_column(JSON)  # validated AlertRuleSpec
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_from_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    company: Mapped[Company | None] = relationship()


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("dedupe_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    related_event_id: Mapped[int | None] = mapped_column(ForeignKey("announcements.id", ondelete="SET NULL"))
    alert_type: Mapped[str] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    as_of: Mapped[date | None] = mapped_column(Date)
    dedupe_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(12), default="new")  # new / seen / dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    company: Mapped[Company] = relationship()


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(32))  # market / filings / documents
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    trace_id: Mapped[str] = mapped_column(String(40))
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)  # names + arg summaries, no document text
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserPreference(Base):
    __tablename__ = "user_preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[dict] = mapped_column(JSON)


# ------------------------------------------------------------------ durable ingestion, versioned financials, coverage


class IngestionJob(Base):
    """One unit of ingestion work (e.g. index one document). Workers claim it with a database lease, so two processes
    can never run the same job at once, and a job abandoned by a crashed worker becomes claimable when its lease expires."""
    __tablename__ = "ingestion_jobs"
    __table_args__ = (UniqueConstraint("key"), Index("ix_jobs_due", "status", "next_attempt_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))                 # index_document
    key: Mapped[str] = mapped_column(String(120))                 # dedupe key, e.g. doc:123
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/retry/succeeded/failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_class: Mapped[str | None] = mapped_column(String(24))   # network/rate_limited/not_found/forbidden/not_pdf/...
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)    # {"stage": "extract", "stages_done": [...]}
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class FinancialSnapshot(Base):
    """One fetch of a company's standardised results from a source, validated before it is promoted."""
    __tablename__ = "financial_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    extraction_version: Mapped[str] = mapped_column(String(32))
    statement_basis: Mapped[str | None] = mapped_column(String(16))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16))               # promoted / rejected
    n_values: Mapped[int] = mapped_column(Integer, default=0)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)     # {"new": n, "restated": n, "unchanged": n, "dropped": n}
    validation: Mapped[dict] = mapped_column(JSON, default=dict)  # {"ok": bool, "errors": [...], "warnings": [...]}


class FinancialObservation(Base):
    """An observed value for one metric/period/basis from one snapshot. Never updated in place: a restatement adds a
    new row and marks the previous one superseded, so history is preserved."""
    __tablename__ = "financial_observations"
    __table_args__ = (Index("ix_obs_key", "company_id", "metric_name", "fiscal_period", "period_type", "statement_basis"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("financial_snapshots.id", ondelete="CASCADE"), index=True)
    fiscal_period: Mapped[str] = mapped_column(String(16))
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(8))
    statement_basis: Mapped[str] = mapped_column(String(16))
    metric_name: Mapped[str] = mapped_column(String(48))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(12), default="crore")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    supersedes_id: Mapped[int | None] = mapped_column(Integer)    # the observation this one restates


class SourceCoverage(Base):
    """What a source has actually covered for a company: the boundaries of the last successful listing and the date
    from which coverage is continuous (no gap between consecutive listings)."""
    __tablename__ = "source_coverage"
    __table_args__ = (UniqueConstraint("company_id", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(48))               # filings_listing
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    items_last: Mapped[int] = mapped_column(Integer, default=0)
    oldest_item_at: Mapped[datetime | None] = mapped_column(DateTime)   # boundaries of the last successful listing
    newest_item_at: Mapped[datetime | None] = mapped_column(DateTime)
    continuous_since: Mapped[datetime | None] = mapped_column(DateTime)  # earliest time with no known gap since
    limited_window: Mapped[bool] = mapped_column(Boolean, default=True)  # source returns only the latest N items


class EventReview(Base):
    """The investor's explicit review of an event (never inferred from a page visit). `filings_seen` records which filings
    of the event existed when it was reviewed, so later filings show up as an update."""
    __tablename__ = "event_reviews"
    __table_args__ = (UniqueConstraint("event_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64))            # announcements.event_group
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="reviewed")   # reviewed / watching
    filings_seen: Mapped[list] = mapped_column(JSON, default=list)
    watch: Mapped[dict | None] = mapped_column(JSON)              # the milestone being monitored (suggested, not an alert)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MonitoringPlan(Base):
    """Investor Handover: what FINN should watch. `spec` is a validated monitoring.PlanSpec; editing creates a new version
    (kept in monitoring_plan_versions) so evaluations and replays always name the exact rule version they used."""
    __tablename__ = "monitoring_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)                 # the investor's own words
    spec: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)


class MonitoringPlanVersion(Base):
    __tablename__ = "monitoring_plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("monitoring_plans.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlanEvaluation(Base):
    """One row per distinct evaluation outcome (plan, version, fingerprint of item ids) — idempotent under re-runs."""
    __tablename__ = "plan_evaluations"
    __table_args__ = (UniqueConstraint("plan_id", "plan_version", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("monitoring_plans.id", ondelete="CASCADE"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(32))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    summary: Mapped[dict] = mapped_column(JSON)
    item_ids: Mapped[dict] = mapped_column(JSON)


class Investigation(Base):
    """Competing Hypotheses: one question about one company, 2–3 hypotheses, and versioned evidence."""
    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    hypotheses: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))               # summary state of the current version
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    reviewed_version: Mapped[int] = mapped_column(Integer, default=1)   # the version the investor last looked at explicitly
    judgement_cache: Mapped[dict] = mapped_column(JSON, default=dict)   # chunk:hypotheses → AI relation (re-used on revisit)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    company: Mapped[Company] = relationship()


class InvestigationVersion(Base):
    __tablename__ = "investigation_versions"
    __table_args__ = (UniqueConstraint("investigation_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReplayRun(Base):
    """Historical Alert Replay: one run of a plan's exact version over past sessions. Separate from live monitoring —
    it never creates alerts or changes the plan. Progress and partial results are saved after every session so a
    cancelled or interrupted run keeps its work and can resume."""
    __tablename__ = "replay_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("monitoring_plans.id", ondelete="CASCADE"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)                       # the exact rule version replayed
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(16), default="public")   # public = available then; indexed = FINN had it then
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/succeeded/cancelled/interrupted/failed
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)     # {done, total, last_session}
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[list] = mapped_column(JSON, default=list)      # one entry per evaluated session
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
