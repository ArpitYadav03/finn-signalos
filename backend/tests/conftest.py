import os
import tempfile
from pathlib import Path

# Isolated DB + data dir, no LLM, set before the app (and its engine) is imported.
_tmp = Path(tempfile.mkdtemp(prefix="finn-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["DATA_DIR"] = str(_tmp / "var")
os.environ["LLM_API_KEY"] = ""
os.environ["ADMIN_TOKEN"] = ""
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["JOBS_WORKER_ENABLED"] = "false"  # tests drive jobs explicitly  # never run background ingestion during tests

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def db():
    from app.core.db import SessionLocal, init_db

    init_db()
    s = SessionLocal()
    yield s
    s.close()
