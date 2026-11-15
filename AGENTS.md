# Notes for coding agents

* Read `README.md`, `docs/architecture.md` and `docs/CHECKLIST.md` first; decisions live in `docs/decisions/`.
* Never let a model do arithmetic: add calculations to `backend/app/services/finance.py` with tests.
* Never fabricate data. Synthetic rows belong in tests only and must be flagged `is_sample`.
* Citations must come from retrieved `DocumentChunk`s or structured metrics — never construct URLs/pages.
* Keep the app runnable: `cd backend && .venv/bin/python -m pytest -q` and `npm run build` in `frontend/` must pass.
* Source adapters must stay polite: honest UA, ≥1 s per host, cache, no bypassing access controls.
* Bump `*_PROMPT_VERSION` in `backend/app/llm/prompts.py` when changing a prompt; re-run `python -m app.cli reanalyse`.
