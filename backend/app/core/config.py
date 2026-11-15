"""Application settings. Everything configurable comes from environment variables (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_prefix="", extra="ignore")

    app_env: str = "development"
    # SQLite by default so the app runs with zero setup; set a postgresql+psycopg URL for Supabase/Render.
    database_url: str = f"sqlite:///{BACKEND_DIR / 'finn.db'}"
    data_dir: Path = BACKEND_DIR / "var"

    # Admin token protects ingestion endpoints. Empty = admin endpoints only allowed from localhost in dev.
    admin_token: str = ""
    # Shared-workspace write key. FINN is single-workspace: everyone who can write shares one portfolio, alert list and
    # preferences. When set, every mutating /api/v1 request must send it as X-Workspace-Key (the web app's server-side
    # proxy adds it; it never reaches the browser). Empty = writes only from localhost (development).
    workspace_write_token: str = ""
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Where the web app lives; opening the backend's root URL redirects here (set to the Vercel URL in prod).
    frontend_url: str = "http://localhost:3000"

    # LLM provider: any OpenAI-compatible endpoint (OpenAI, Groq, OpenRouter, Ollama). Empty key = deterministic mode.
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model_fast: str = "gpt-4o-mini"        # bulk classification / extraction
    llm_model_reasoning: str = "gpt-4.1"       # research synthesis, guidance analysis
    llm_embedding_model: str = "text-embedding-3-small"
    # Comma-separated models tried in order when the primary is rate-limited, overloaded or retired.
    llm_fallback_models_fast: str = ""
    llm_fallback_models_reasoning: str = ""
    llm_max_calls_per_run: int = 40            # hard cap so a loop can never run up a bill
    # USD per 1M tokens (input, output) for cost estimates in agent_runs
    llm_price_fast_in: float = 0.15
    llm_price_fast_out: float = 0.60
    llm_price_reasoning_in: float = 2.0
    llm_price_reasoning_out: float = 8.0

    # Source adapters
    # Honest, plain UA. (BSE's CDN rejects UAs containing parentheses.)
    http_user_agent: str = "FINN-SignalOS/0.1 research prototype"
    http_min_interval_s: float = 1.0           # politeness delay per host
    screener_cache_hours: float = 6.0
    max_pdf_mb: int = 40
    max_pdf_pages: int = 400

    # Daily refresh: prices, filings, analysis, new documents for Research, alerts.
    jobs_worker_enabled: bool = True               # in-process worker for durable ingestion jobs (retries, recovery)
    scheduler_enabled: bool = True                 # in-process scheduler (set false when an external cron drives ingestion)
    daily_refresh_times_ist: str = "08:30,18:30"   # pre-market (overnight filings) and post-close (EOD prices)
    catch_up_after_hours: float = 12.0             # on startup, refresh if the last successful run is older than this
    daily_max_new_documents: int = 60              # cap per run so a first run can't run away
    daily_transcripts_per_company: int = 2
    daily_index_annual_reports: bool = True

    # Default alert thresholds (user-overridable via alert rules)
    default_price_move_pct: float = 4.0
    default_volume_multiple: float = 2.0

    @field_validator("database_url", "data_dir", mode="before")
    @classmethod
    def _blank_means_default(cls, v, info):
        # `DATABASE_URL=` (blank, as in .env.example) should fall back to the default, not to "".
        if v is None or (isinstance(v, str) and not v.strip()):
            return cls.model_fields[info.field_name].default
        return v

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    def deployment_problems(self) -> list[str]:
        """Unsafe public configurations, reported at startup (and fatal in production)."""
        out = []
        if self.is_production:
            if not self.admin_token or len(self.admin_token) < 24:
                out.append("ADMIN_TOKEN must be set (24+ characters) in production")
            if not self.workspace_write_token or len(self.workspace_write_token) < 24:
                out.append("WORKSPACE_WRITE_TOKEN must be set (24+ characters) in production")
            if any(o == "*" for o in self.cors_list):
                out.append("CORS_ORIGINS must list explicit origins, not *")
            if any("localhost" in o or "127.0.0.1" in o for o in self.cors_list):
                out.append("CORS_ORIGINS contains localhost in production")
        return out

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "docs").mkdir(exist_ok=True)
    (s.data_dir / "http_cache").mkdir(exist_ok=True)
    return s
