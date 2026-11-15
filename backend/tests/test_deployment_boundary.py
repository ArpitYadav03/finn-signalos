"""Public deployment boundary: shared-workspace writes need the workspace key; admin needs its own token; reads stay
open; unsafe production settings are detected."""
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings


def test_writes_require_workspace_key_when_configured(db):
    from app.main import app

    s = get_settings()
    old = s.workspace_write_token
    s.workspace_write_token = "k" * 32
    try:
        with TestClient(app) as c:
            assert c.get("/api/v1/portfolio").status_code == 200                       # reads stay public
            r = c.put("/api/v1/preferences", json={"price_move_pct": 5})
            assert r.status_code == 401 and "workspace key" in r.json()["detail"]
            assert c.post("/api/v1/research", json={"ticker": "TCS", "question": "x?"}).status_code == 401
            assert c.delete("/api/v1/alert-rules/1").status_code == 401
            bad = c.put("/api/v1/preferences", json={"price_move_pct": 5}, headers={"X-Workspace-Key": "wrong"})
            assert bad.status_code == 401
            ok = c.put("/api/v1/preferences", json={"price_move_pct": 4}, headers={"X-Workspace-Key": "k" * 32})
            assert ok.status_code == 200
    finally:
        s.workspace_write_token = old


def test_production_refuses_unsafe_settings():
    bad = Settings(app_env="production", admin_token="", workspace_write_token="short", cors_origins="*,http://localhost:3000")
    probs = bad.deployment_problems()
    assert any("ADMIN_TOKEN" in p for p in probs) and any("WORKSPACE_WRITE_TOKEN" in p for p in probs)
    assert any("not *" in p for p in probs) and any("localhost" in p for p in probs)
    good = Settings(app_env="production", admin_token="a" * 32, workspace_write_token="w" * 32,
                    cors_origins="https://finn.example.com")
    assert good.deployment_problems() == []
    assert Settings(app_env="development").deployment_problems() == []
