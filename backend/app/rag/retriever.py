"""Hybrid retrieval over a company's primary-document chunks.

* BM25 keyword scoring always runs (no external service, exact matches on tickers/figures/terms).
* If chunks have embeddings and an LLM key is configured, cosine similarity is fused in with
  reciprocal-rank fusion. Filters: company, document type, speaker role, reporting period.
At prototype scale (a few thousand chunks per company) in-process scoring is fast; ADR-003 records the
pgvector upgrade path for larger corpora.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk

STOP = set("""a an the of and or to in on for with by at from as is are was were be been it its this that these those
we our you your they their i he she what which who whom how why when where do does did can could would should will
about into over than then there here also any all more most such not no yes so if but very just has have had""".split())
# Words that frame a question ("what has management said about…") but say nothing about its topic.
FRAMING = set("""said say says saying tell told talk talked talking management management's company company's latest recent
think view views comment comments commentary explain give me please mention mentioned discuss discussed update""".split())
SYNONYMS = {
    "margin": ["ebitda", "ebit", "operating", "profitability"],
    "debt": ["borrowings", "leverage", "loan"],
    "guidance": ["expect", "outlook", "target", "aspiration"],
    "growth": ["grow", "growing", "grew"],
    "capex": ["capital", "expenditure", "capacity"],
    "demand": ["pipeline", "orders", "deal"],
    "cash": ["cfo", "conversion", "receivables"],
    "risk": ["headwind", "challenge", "pressure", "uncertainty"],
}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9%.\-]*", text.lower()) if t not in STOP and len(t) > 1]


def expand(tokens: list[str]) -> list[str]:
    out = list(tokens)
    for t in tokens:
        out += SYNONYMS.get(t, [])
    return out


@dataclass
class Hit:
    chunk: DocumentChunk
    document: Document
    score: float
    matched: int = 0        # distinct (unexpanded) query topic terms present in the chunk
    query_terms: int = 0    # how many topic terms the query had


def query_terms(query: str) -> list[str]:
    return [t for t in dict.fromkeys(tokenize(query)) if t not in FRAMING]


def looks_like_toc(text: str) -> bool:
    """Tables of contents / indexes: many small page numbers in ascending order, or an explicit heading."""
    if re.search(r"what'?s inside|table of contents|^\s*contents\b|^\s*index\b", text[:200], re.I | re.M):
        return True
    nums = [int(n) for n in re.findall(r"(?<![\d.,₹])\b(\d{1,3})\b(?![\d.,%])", text)]
    if len(nums) < 8:
        return False
    rising = sum(1 for a, b in zip(nums, nums[1:]) if b > a)
    return rising / (len(nums) - 1) >= 0.7


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(db: Session, company_id: int, query: str, *, k: int = 6, document_types: list[str] | None = None,
           speaker_role: str | None = None, period: str | None = None, query_embedding: list[float] | None = None,
           document_ids: list[int] | None = None, contextual: bool = False, rerank: bool = False) -> list[Hit]:
    """BM25 keyword retrieval (default). Options under evaluation (evals/scripts/retrieval_benchmark.py) — adopted only
    after a measured improvement on held-out cases:
    * contextual: score each chunk with its document context prepended (title, period, section, speaker role);
    * query_embedding: fuse cosine similarity over stored embeddings (hybrid, reciprocal-rank fusion);
    * rerank: re-order the top 30 by query-phrase proximity (adjacent query terms appearing together)."""
    q = select(DocumentChunk, Document).join(Document, Document.id == DocumentChunk.document_id).where(
        Document.company_id == company_id, Document.status == "indexed")
    if document_ids:
        q = q.where(Document.id.in_(document_ids))
    elif document_types:
        q = q.where(Document.document_type.in_(document_types))
    else:
        q = q.where(Document.document_type.in_(["concall_transcript", "annual_report", "presentation", "exchange_filing"]))
    if speaker_role:
        q = q.where((DocumentChunk.speaker_role == speaker_role) | (DocumentChunk.speaker_role.is_(None)))
    if period:
        q = q.where(Document.reporting_period == period)
    rows = db.execute(q).all()
    if not rows:
        return []
    terms = query_terms(query) or tokenize(query)
    qtok = expand(terms)
    if contextual:
        docs_tokens = [tokenize(f"{d.title} {d.reporting_period or ''} {c.section or ''} {c.speaker_role or ''} {c.content}")
                       for c, d in rows]
    else:
        docs_tokens = [tokenize(c.content) for c, _ in rows]
    n = len(rows)
    avgdl = sum(len(t) for t in docs_tokens) / n
    df = Counter()
    for toks in docs_tokens:
        df.update(set(toks))
    k1, b = 1.4, 0.75
    bm25 = []
    for toks in docs_tokens:
        tf = Counter(toks)
        s = 0.0
        for t in set(qtok):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(toks) / avgdl))
        bm25.append(s)
    ranked_kw = sorted(range(n), key=lambda i: bm25[i], reverse=True)
    fused: dict[int, float] = {}
    for rank, i in enumerate(ranked_kw[:50]):
        if bm25[i] > 0:
            fused[i] = fused.get(i, 0) + 1 / (60 + rank)
    if query_embedding is not None:
        sims = [(i, _cosine(query_embedding, c.embedding)) for i, (c, _) in enumerate(rows) if c.embedding]
        for rank, (i, _) in enumerate(sorted(sims, key=lambda x: x[1], reverse=True)[:50]):
            fused[i] = fused.get(i, 0) + 1 / (60 + rank)
    # Light preference for management over moderator boilerplate; table-heavy chunks are down-weighted for
    # narrative questions (numbers should come from structured metrics, not from parsing table fragments).
    query_has_digits = bool(re.search(r"\d", query))
    for i in fused:
        c, d = rows[i]
        toks = docs_tokens[i]
        if toks and not query_has_digits and sum(t[0].isdigit() for t in toks) / len(toks) > 0.35:
            fused[i] *= 0.5
        if c.speaker_role == "moderator":
            fused[i] *= 0.3
        if looks_like_toc(c.content):
            fused[i] *= 0.1
        if c.speaker_role == "management":
            fused[i] *= 1.15
    top = sorted(fused.items(), key=lambda x: x[1], reverse=True)[: (30 if rerank else k)]
    if rerank and top:
        pairs = list(zip(qtok, qtok[1:]))
        def prox(i: int) -> float:
            toks = docs_tokens[i]
            bigrams = set(zip(toks, toks[1:]))
            return sum(1 for p in pairs if p in bigrams)
        top = sorted(top, key=lambda x: (prox(x[0]), x[1]), reverse=True)[:k]
    tset = set(terms)
    return [Hit(rows[i][0], rows[i][1], round(s, 5), len(tset & set(docs_tokens[i])), len(tset)) for i, s in top]


def citation_for(hit: Hit, *, excerpt_len: int = 420) -> dict:
    c, d = hit.chunk, hit.document
    return {
        "kind": "document",
        "document_id": d.id,
        "document_title": d.title,
        "source_url": d.source_url,
        "document_type": d.document_type,
        "publication_date": d.publication_date.isoformat() if d.publication_date else None,
        "reporting_period": d.reporting_period,
        "page_number": c.page_number,
        "chunk_id": c.id,
        "speaker": c.speaker,
        "speaker_role": c.speaker_role,
        "section": c.section,
        "relevant_excerpt": c.content[:excerpt_len] + ("…" if len(c.content) > excerpt_len else ""),
    }
