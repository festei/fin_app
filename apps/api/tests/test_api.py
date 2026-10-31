from fastapi.testclient import TestClient

from fin_api.main import app

client = TestClient(app)


def test_health_and_ready():
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


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
