"""Evidence contracts shared by Investor Handover, Competing Hypotheses and Alert Replay.

Every displayed item is exactly one of:
* ``fact``          — a financial figure from a source (company, metric, value with sign, unit, period, basis, source);
* ``calculation``   — a deterministic result that keeps its formula and the ids of its input facts;
* ``document``      — a claim backed by a passage of a specific document version (content hash) and page/chunk; the
                      quoted passage must occur verbatim in that chunk, or the item is not created;
* ``interpretation``— a labelled reading of the above, never presented as fact;
* ``insufficient``  — an explicit statement that the evidence does not settle the point.

Builders here return ``None`` rather than a weakened item when a contract cannot be met.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

Kind = Literal["fact", "calculation", "document", "interpretation", "insufficient"]


class FinancialFact(BaseModel):
    company: str
    metric: str
    label: str
    value: float
    unit: str                      # "₹ crore", "%", "pp", "×", "days", "₹"
    period: str                    # FY26, Q2FY27, 2026-11-13
    period_type: str               # annual / quarter / session
    basis: str | None              # consolidated / standalone
    source: dict                   # kind, title, url, primary page if found
    display: str


class CalculatedResult(BaseModel):
    company: str
    metric: str
    label: str
    value: float
    unit: str
    period: str
    basis: str | None
    formula: str
    expression: str
    inputs: list[FinancialFact]
    display: str


class DocumentClaim(BaseModel):
    company: str
    document_id: int
    document_title: str
    document_type: str
    document_version: str | None   # content hash of the PDF the passage was read from
    reporting_period: str | None
    published: str | None
    page: int
    chunk_id: int
    passage: str                   # verbatim quote (validated against the chunk)
    speaker: str | None = None
    source_url: str | None = None


class EvidenceItem(BaseModel):
    kind: Kind
    relation: Literal["supports", "challenges", "context", "does_not_distinguish", "missing"] | None = None
    statement: str                 # one concise line in plain language
    explanation: str | None = None
    limitations: list[str] = Field(default_factory=list)
    fact: FinancialFact | None = None
    calculation: CalculatedResult | None = None
    document: DocumentClaim | None = None


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()


def document_claim(db: Session, chunk_id: int, quote: str | None = None, *, max_len: int = 420) -> DocumentClaim | None:
    """Build a document claim; ``quote`` must appear verbatim (whitespace-normalised) in the chunk, else None."""
    from app.models import DocumentChunk

    ch = db.get(DocumentChunk, chunk_id)
    if ch is None:
        return None
    text = re.sub(r"\s+", " ", ch.content).strip()
    if quote:
        q = re.sub(r"\s+", " ", quote).strip().strip("“”\"'")
        if len(q) < 12 or _norm(q) not in _norm(text):
            return None
        passage = q[:max_len]
    else:
        passage = text[:max_len]
    d = ch.document
    return DocumentClaim(company=d.company.ticker, document_id=d.id, document_title=d.title, document_type=d.document_type,
                         document_version=d.content_hash, reporting_period=d.reporting_period,
                         published=d.publication_date.isoformat() if d.publication_date else None, page=ch.page_number,
                         chunk_id=ch.id, passage=passage, speaker=ch.speaker, source_url=d.source_url)


def fact_from_claim(c) -> FinancialFact:
    return FinancialFact(company=c.company, metric=c.metric, label=c.label, value=c.value, unit=c.unit, period=c.period,
                         period_type=c.period_type, basis=c.basis, source=c.source, display=c.display)


def calculation_from_claim(c, registry) -> CalculatedResult | None:
    inputs = [registry.get(i) for i in c.inputs]
    if c.kind != "calc" or any(x is None for x in inputs):
        return None
    return CalculatedResult(company=c.company, metric=c.metric, label=c.label, value=c.value, unit=c.unit, period=c.period,
                            basis=c.basis, formula=c.formula or "", expression=c.expression or "",
                            inputs=[fact_from_claim(x) for x in inputs], display=c.display)
