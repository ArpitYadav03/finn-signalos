from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.ingestion.providers import normalise_ticker
from app.models import Company

_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(limit: int = 20, per_seconds: int = 60):
    """Tiny in-memory sliding-window limiter for expensive endpoints (single-instance deployment)."""

    def dep(request: Request):
        key = f"{request.client.host if request.client else 'anon'}:{request.url.path.split('/')[3] if request.url.path.count('/') > 2 else ''}"
        now = time.monotonic()
        q = _hits[key]
        while q and now - q[0] > per_seconds:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many requests — slow down a little.")
        q.append(now)

    return dep


def require_admin(request: Request, x_admin_token: str | None = Header(None)):
    token = get_settings().admin_token
    if token:
        if not hmac.compare_digest((x_admin_token or "").encode(), token.encode()):
            raise HTTPException(401, "Admin token required")
        return
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "Admin endpoints are local-only until ADMIN_TOKEN is configured")


def company_or_404(ticker: str, db: Session = Depends(get_db)) -> Company:
    try:
        t = normalise_ticker(ticker)
    except ValueError as e:
        raise HTTPException(422, str(e))
    c = db.scalars(select(Company).where(Company.ticker == t)).first()
    if c is None:
        raise HTTPException(404, f"{t} is not indexed yet. POST /api/v1/companies/{t}/index or ask a research question.")
    return c


LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient")


def write_allowed(request: Request) -> tuple[bool, int, str]:
    """Shared-workspace mutations: allowed with the workspace key, or from localhost when no key is configured
    (development only — production refuses to start without a key)."""
    s = get_settings()
    if s.workspace_write_token:
        got = request.headers.get("x-workspace-key", "")
        if hmac.compare_digest(got.encode(), s.workspace_write_token.encode()):
            return True, 200, ""
        return False, 401, "This FINN workspace is read-only without the workspace key."
    host = request.client.host if request.client else ""
    if host in LOCAL_HOSTS:
        return True, 200, ""
    return False, 403, "Writes are local-only until WORKSPACE_WRITE_TOKEN is configured."
