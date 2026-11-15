from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from app.api.routes import router
from app.api.plans import router as plans_router
from app.api.investigations import router as investigations_router
from app.core.config import get_settings
from app.core.db import SessionLocal, engine, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("finn")

_s = get_settings()
_problems = _s.deployment_problems()
if _problems and _s.is_production:
    raise RuntimeError("Unsafe production configuration: " + "; ".join(_problems))
app = FastAPI(title="FINN SignalOS API", version="0.1.0",
              description="Evidence-first investment intelligence: Push (signals, alerts) and Pull (primary-source research).",
              docs_url=None if _s.is_production else "/docs", redoc_url=None, openapi_url=None if _s.is_production else "/openapi.json")
# Explicit origins, methods and headers: browsers in production talk to the web app's own proxy (same origin), so the
# API only needs to allow the deployed web origin.
app.add_middleware(CORSMiddleware, allow_origins=_s.cors_list, allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Content-Type", "X-Workspace-Key", "X-Admin-Token"])


@app.middleware("http")
async def _protect_shared_writes(request, call_next):
    """Every mutating /api/v1 request (including research, which can trigger downloads and model spend) needs the
    workspace key; admin routes additionally need the admin token (checked in the route)."""
    from app.api.deps import write_allowed

    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/v1/") and not path.startswith("/api/v1/admin/"):
        ok, code, msg = write_allowed(request)
        if not ok:
            return JSONResponse({"detail": msg}, status_code=code)
    return await call_next(request)


app.include_router(router)
app.include_router(plans_router)
app.include_router(investigations_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    from app.ingestion.pipeline import seed

    db = SessionLocal()
    try:
        seed(db)
        from app.services.replay import recover_stale

        recover_stale(db, at_startup=True)   # replays cut off by a restart become 'interrupted' (resumable)
    finally:
        db.close()
    from app.workers import job_worker, scheduler

    scheduler.start()
    if get_settings().jobs_worker_enabled:
        job_worker.start()   # also recovers jobs abandoned by a previous process (expired leases)
    log.info("FINN ready. AI mode: %s", "llm" if get_settings().llm_enabled else "deterministic")


@app.on_event("shutdown")
def _shutdown() -> None:
    from app.workers import job_worker, scheduler

    scheduler.stop()
    job_worker.stop()


@app.get("/", include_in_schema=False)
def root():
    # The backend is an API; anyone opening it in a browser wants the app (API docs stay at /docs).
    return RedirectResponse(get_settings().frontend_url)


@app.get("/health")
def health():
    return {"status": "ok", "service": "finn-signalos"}


@app.get("/ready")
def ready():
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ready", "database": engine.dialect.name, "ai_mode": "llm" if get_settings().llm_enabled else "deterministic"}
    except Exception as e:  # pragma: no cover
        return JSONResponse({"status": "not_ready", "error": str(e)[:200]}, status_code=503)
