"""Typed API contracts (request bodies strictly validated; responses typed at the top level)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.alerts import AlertRuleSpec


class Citation(BaseModel):
    kind: str
    document_id: int | None = None
    document_title: str | None = None
    source_url: str | None = None
    document_type: str | None = None
    publication_date: str | None = None
    reporting_period: str | None = None
    page_number: int | None = None
    chunk_id: int | None = None
    speaker: str | None = None
    speaker_role: str | None = None
    relevant_excerpt: str | None = None


class SignalCard(BaseModel):
    id: int
    ticker: str
    company_name: str
    category: str
    category_label: str
    headline: str
    summary: str | None
    why_it_matters: str | None
    importance_score: float
    importance_level: Literal["high", "medium", "low", "routine"]
    reasons: list[dict]
    key_metrics: list[dict]
    facts: dict
    citations: list[Citation]
    published_at: str
    processed_at: str
    source: str
    source_url: str
    is_sample: bool
    requires_review: bool
    related: list[dict] = []
    market: dict | None = None
    analysis_method: str
    what_changed: str | None = None
    supporting: list[dict] = []   # Event DNA: one entry per filing, oldest first, with what it added
    event_key: str | None = None
    milestones: list[dict] = []   # next evidence to watch (suggested monitoring, not alerts)
    promise_links: list[dict] = []  # Promise Ledger items connected by documents
    review: dict | None = None    # the investor's explicit review state, if any


class Briefing(BaseModel):
    window: dict
    generated_at: str
    coverage: dict = {}
    collection: dict = {}
    freshness: dict
    counts: dict
    signals: list[SignalCard]
    routine: list[SignalCard]
    anomalies: list[dict]
    alerts: list[dict]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class ResearchRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=24)
    question: str = Field(min_length=2, max_length=500)
    filing_id: int | None = None
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class AssistantRequest(BaseModel):
    message: str = Field(min_length=2, max_length=500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)


class ApplyActionsRequest(BaseModel):
    actions: list[dict] = Field(min_length=1, max_length=10)


class ResearchResponse(BaseModel):
    ticker: str
    company_name: str
    question: str
    intents: list[str]
    mode: str
    prompt_version: str
    answer_markdown: str
    evidence: list[dict[str, Any]]
    metrics_used: list[str]
    caveats: list[str]
    coverage: dict
    trace_id: str
    steps: list[dict] = []
    unverified_numbers: list[str] = []
    claims: dict[str, dict[str, Any]] = {}      # F#/C# -> fact or calculation (with inputs, formula, source)
    verification: dict[str, Any] = {}
    follow_up: str | None = None


class AlertRuleCreate(BaseModel):
    name: str | None = Field(None, max_length=160)
    spec: AlertRuleSpec
    created_from_text: str | None = Field(None, max_length=500)


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(None, max_length=160)
    enabled: bool | None = None
    spec: AlertRuleSpec | None = None


class AlertRuleParseRequest(BaseModel):
    text: str = Field(min_length=3, max_length=500)


class AlertStatusUpdate(BaseModel):
    status: Literal["new", "seen", "dismissed"]


class PreferencesUpdate(BaseModel):
    price_move_pct: float | None = Field(None, gt=0, le=50)
    volume_multiple: float | None = Field(None, gt=0, le=50)
    watched_categories: list[str] | None = None
