# ADR-003: SQLite locally, Postgres in production; embeddings as JSON for now

**Status:** accepted

**Decision.** One SQLAlchemy model set runs on SQLite (zero setup for evaluators) and PostgreSQL (Supabase/Render, `docker compose` ships pgvector). Embeddings, when an LLM key exists, are stored as JSON arrays and scored in-process; BM25 always runs.

**Why not pgvector immediately?** Per-company corpora are ~1–2k chunks; scoring is milliseconds, and a JSON column keeps SQLite parity and tests simple. **Upgrade path:** add a `vector(1536)` column + HNSW index on Postgres and push the cosine step into SQL; the retriever interface doesn't change.
