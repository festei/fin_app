from fastapi.testclient import TestClient

from fin_api.main import app

client = TestClient(app)


def clear_db_env(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)


def test_health_and_ready_without_a_database(monkeypatch):
    clear_db_env(monkeypatch)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready", "db": "not configured"}


def test_ready_checks_a_configured_database(monkeypatch, tmp_path):
    clear_db_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ready.db'}")
    assert client.get("/readyz").json() == {"status": "ready", "db": "ok"}


def test_not_ready_when_the_configured_database_is_unreachable(monkeypatch, tmp_path):
    clear_db_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'missing-dir' / 'x.db'}")
    r = client.get("/readyz")
    assert r.status_code == 503
    assert "database unavailable" in r.json()["detail"]
    assert "missing-dir" not in r.json()["detail"]  # no connection details leaked
    assert client.get("/healthz").status_code == 200  # liveness is independent of the DB


def test_features_telegram_off_by_default(monkeypatch):
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    assert client.get("/api/features").json()["telegram_enabled"] is False


def test_score_endpoint():
    body = {
        "metrics": {
            "ticker": "TEST",
            "as_of": "2025-01-01T00:00:00",
            "pe": 10,
            "pb": 1.2,
            "current_ratio": 2.5,
            "debt_to_equity": 0.4,
            "dividend_years": 20,
            "earnings_growth": 0.05,
        }
    }
    r = client.post("/api/score", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["conviction"] == 100.0
    assert data["signal"]["kind"] == "emerging"
