#!/usr/bin/env bash
# Start the FastAPI backend on :8000 (creates the venv on first run).
set -euo pipefail
cd "$(dirname "$0")/../backend"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"; }
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" ${FINN_RELOAD:+--reload}
