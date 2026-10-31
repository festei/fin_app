import pytest
from fastapi.testclient import TestClient

from fin_api.main import app, dispose_engines

client = TestClient(app)


@pytest.fixture(autouse=True)
def _close_cached_engines():
    """The API caches one engine per database URL; close them so tests do not leak pools."""
    yield
    dispose_engines()


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


def seeded_db(monkeypatch, tmp_path):
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from fin_core.db import migrate, repo
    from fin_core.db.session import make_engine, session_scope

    clear_db_env(monkeypatch)
    url = f"sqlite:///{tmp_path / 'score.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = make_engine(url)

    def f(concept, tag, value, unit="USD", instant=False):
        end = date(2024, 12, 31)
        return dict(
            concept=concept,
            tag=tag,
            unit=unit,
            period_start=end if instant else date(2024, 1, 1),
            period_end=end,
            instant=instant,
            fiscal_year=2024,
            fiscal_period="FY",
            form="10-K",
            filed=date(2025, 2, 15),
            accession=f"a-{concept}",
            value=Decimal(str(value)),
        )

    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "ACME"}])
        sid = repo.get_security(s, "ACME").id
        repo.upsert_facts(
            s,
            sid,
            [
                f("EpsDiluted", "us-gaap/EarningsPerShareDiluted", 2.5, unit="USD/shares"),
                f("StockholdersEquity", "us-gaap/StockholdersEquity", 1000, instant=True),
                f("Assets", "us-gaap/Assets", 2000, instant=True),
            ],
        )
        repo.upsert_prices(
            s,
            sid,
            [
                dict(
                    ts=datetime(2025, 3, 3, 21, tzinfo=UTC),
                    open=25.0,
                    high=25.0,
                    low=25.0,
                    close=25.0,
                    volume=1,
                )
            ],
        )


def test_score_a_stored_ticker(monkeypatch, tmp_path):
    seeded_db(monkeypatch, tmp_path)
    r = client.get("/api/securities/acme/score", params={"as_of": "2025-03-10"})
    assert r.status_code == 200
    body = r.json()
    assert body["metrics"]["ticker"] == "ACME" and body["metrics"]["pe"] == 10.0
    assert body["philosophy_results"]["graham"]["score"] > 0
    # shares outstanding and others are missing: reported, not hidden
    assert body["metrics"]["pb"] is None


def test_score_ticker_surfaces_data_gaps_as_warnings(monkeypatch, tmp_path):
    seeded_db(monkeypatch, tmp_path)
    r = client.get("/api/securities/ACME/score", params={"as_of": "2023-01-01"})
    assert r.status_code == 200
    assert "no annual fundamentals available as of this date" in r.json()["metrics"]["warnings"]


def test_score_unknown_ticker_is_404_and_no_database_is_503(monkeypatch, tmp_path):
    seeded_db(monkeypatch, tmp_path)
    assert client.get("/api/securities/NOPE/score").status_code == 404
    clear_db_env(monkeypatch)
    r = client.get("/api/securities/ACME/score")
    assert r.status_code == 503 and "not configured" in r.json()["detail"]


def test_score_ticker_returns_503_when_the_database_is_down_not_500(monkeypatch, tmp_path):
    clear_db_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'missing-dir' / 'x.db'}")
    r = client.get("/api/securities/ACME/score")
    assert r.status_code == 503
    assert "database unavailable" in r.json()["detail"]
    assert "missing-dir" not in r.json()["detail"]  # no connection details leaked


def test_score_ticker_rejects_a_future_as_of(monkeypatch, tmp_path):
    seeded_db(monkeypatch, tmp_path)
    r = client.get("/api/securities/ACME/score", params={"as_of": "2999-01-01"})
    assert r.status_code == 422 and "in the future" in r.json()["detail"]


def test_dispose_engines_closes_and_forgets_the_cached_engines(monkeypatch, tmp_path):
    from fin_api import main

    seeded_db(monkeypatch, tmp_path)
    assert client.get("/readyz").status_code == 200
    assert main._engines  # an engine was created and cached
    dispose_engines()
    assert main._engines == {}


def test_response_carries_all_abstained_and_allows_no_conviction():
    from fin_api.main import ScoreResponse

    body = {
        "metrics": {
            "ticker": "T", "as_of": "2025-01-01T00:00:00", "pe": 10, "pb": 1.2,
            "current_ratio": 2.5, "debt_to_equity": 0.4, "dividend_years": 20,
            "earnings_growth": 0.05,
        }
    }  # fmt: skip
    data = client.post("/api/score", json=body).json()
    assert data["all_abstained"] is False and data["conviction"] == 100.0

    # when no philosophy can be evaluated the conviction is null ("no view"), never 0.0
    none = ScoreResponse(
        conviction=None, all_abstained=True, explanation="No philosophy could be evaluated.",
        philosophy_results={}, signal=None,
    )  # fmt: skip
    assert none.model_dump()["conviction"] is None and none.all_abstained is True
