# Deployment

**Status: deployment-ready, not deployed.** No hosting credentials were available during the build. Everything
below is configured in the repo; the remaining steps need your accounts.

## 1. Database — Supabase (free tier)

1. Create a project (region `ap-south-1`).
2. Copy the **pooled** connection string (port 6543) and convert it for SQLAlchemy:
   `postgresql+psycopg://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require`
3. Optional (for vector search in SQL later): `create extension if not exists vector;`

Migrations run automatically when the backend container starts (`alembic upgrade head`).

## 2. Backend — Render (Docker web service)

* Root directory: `backend` · Runtime: Docker · Health check path: `/ready`
* Environment:
  * `DATABASE_URL` — from step 1
  * `APP_ENV=production` — the API refuses to start with unsafe settings below and disables `/docs`
  * `ADMIN_TOKEN` — 24+ random characters (protects `/api/v1/admin/*`)
  * `WORKSPACE_WRITE_TOKEN` — 24+ random characters; required on every mutating request (shared single workspace)
  * `CORS_ORIGINS` — your Vercel URL only, e.g. `https://finn-signalos.vercel.app` (no `*`, no localhost)
  * `FRONTEND_URL` — the same Vercel URL (the backend root redirects there)
  * `JOBS_WORKER_ENABLED=true` — in-process ingestion worker (retries, restart recovery); extra workers may run as
    `python -m app.cli worker` against the same database
  * `LLM_API_KEY` (optional), `LLM_BASE_URL` (optional, any OpenAI-compatible endpoint), model names
* Disk: downloaded PDFs live in `/app/var`. Without a persistent disk they are re-downloaded after a restart
  (content-hashed, so no duplicate rows). Attach a small Render disk at `/app/var` to keep them.
* First data load (from your machine): `curl -X POST "$API/api/v1/admin/ingest/daily" -H "X-Admin-Token: $TOKEN"`

## 3. Frontend — Vercel

* Import the repo, root directory `frontend`, framework Next.js.
* Env (server-side only — never `NEXT_PUBLIC_*`): `FINN_API_URL=https://<your-render-service>.onrender.com`,
  `FINN_WORKSPACE_KEY=<same value as WORKSPACE_WRITE_TOKEN>`, optional `FINN_SITE_PASSWORD` (HTTP basic auth on every
  page). Do **not** set `NEXT_PUBLIC_API_URL` in production: browsers call `/api/finn/*`, which forwards to the API and
  adds the key server-side, so no credential reaches the browser.
* Node 22 (Vercel default).
* **Scope:** FINN is one shared workspace. Everyone who passes the site password shares one portfolio, alert list and
  preferences; there are no per-user accounts.

## 4. Scheduled ingestion

Locally (and on always-on hosts) the backend's built-in scheduler refreshes everything at 08:30 and 18:30 IST and
catches up on startup. On hosts that sleep when idle (Render free tier), use the GitHub Actions cron below and set
`SCHEDULER_ENABLED=false` on the backend so runs aren't duplicated.

### GitHub Actions

`.github/workflows/daily-ingest.yml` calls the admin endpoint at 16:30 IST on weekdays. Add repository secrets
`FINN_API_URL` and `FINN_ADMIN_TOKEN`. It is idempotent: re-runs add only new bars/filings/alerts.

## 5. Verify

* `GET /health` → `{"status":"ok"}` · `GET /ready` → database dialect + AI mode
* Open the Vercel URL on a phone → Briefing shows "filings checked … ago" and the EOD date.

## Security checklist

* Secrets only in server env vars; `.env` is git-ignored; `.env.example` has no values.
* Admin endpoints require `X-Admin-Token` (local-only when no token is set).
* Rate limits on `/research`, `/companies/{t}/index`, `/alert-rules/parse` (in-memory, single instance).
* Ticker input validated (`^[A-Z0-9][A-Z0-9&-]{0,19}$`); PDF downloads https-only, allow-listed hosts or
  public-IP IR hosts, 40 MB cap, `%PDF-` magic check; files served only from the document store directory.
* Retrieved document text is treated as untrusted data (see `llm/prompts.py`).
