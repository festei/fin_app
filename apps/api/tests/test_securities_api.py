"""Securities search, price history and score history endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fin_api.main import app, dispose_engines
from fin_core.db import migrate, repo, signals_repo
from fin_core.db.session import make_engine, session_scope

client = TestClient(app)


@pytest.fixture(autouse=True)
def _close_engines():
    yield
    dispose_engines()


@pytest.fixture
def db(monkeypatch, tmp_path):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    url = f"sqlite:///{tmp_path / 'sec.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = make_engine(url)
    with session_scope(engine) as s:
        repo.upsert_securities(
            s,
            [
                {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Technology"},
                {"ticker": "AMZN", "name": "Amazon.com", "sector": "Consumer"},
                {"ticker": "MSFT", "name": "Microsoft", "sector": "Technology"},
                {"ticker": "TLT", "name": "iShares 20+ Year Treasury", "kind": "etf"},
                {"ticker": "OLD", "name": "Retired Co", "active": False},
            ],
        )
        signals_repo.watchlist_add(s, repo.get_security(s, "MSFT").id)
    yield engine
    engine.dispose()


def tickers(r):
    return [x["ticker"] for x in r.json()]


def test_search_by_ticker_prefix_name_sector_and_kind(db):
    assert tickers(client.get("/api/securities")) == ["AAPL", "AMZN", "MSFT", "TLT"]  # no inactive
    assert tickers(client.get("/api/securities?q=a")) == [
        "AAPL",
        "AMZN",
        "TLT",
    ]  # ticker prefix or name fragment
    assert tickers(client.get("/api/securities?q=am")) == ["AMZN"]
    assert tickers(client.get("/api/securities?q=soft")) == ["MSFT"]  # name fragment
    assert tickers(client.get("/api/securities?sector=Technology")) == ["AAPL", "MSFT"]
    assert tickers(client.get("/api/securities?kind=etf")) == ["TLT"]
    assert tickers(client.get("/api/securities?limit=2")) == ["AAPL", "AMZN"]
    assert client.get("/api/securities?q=zzz").json() == []


def test_search_flags_the_watchlist_and_ignores_like_wildcards(db):
    rows = {x["ticker"]: x for x in client.get("/api/securities").json()}
    assert rows["MSFT"]["watched"] is True and rows["AAPL"]["watched"] is False
    assert rows["TLT"]["kind"] == "etf"
    assert client.get("/api/securities?q=%25").json() != []  # '%' is not a wildcard
    assert tickers(client.get("/api/securities?q=_")) == tickers(client.get("/api/securities?q="))


def add_bars(engine, ticker, n):
    today = datetime.now(UTC).replace(hour=21, minute=0, second=0, microsecond=0)
    with session_scope(engine) as s:
        repo.upsert_prices(
            s,
            repo.get_security(s, ticker).id,
            [
                {"ts": today - timedelta(days=n - 1 - i), "open": i, "high": i, "low": i,
                 "close": float(i + 1), "volume": 10}
                for i in range(n)
            ],
        )  # fmt: skip


def test_prices_are_oldest_first_within_the_window(db):
    add_bars(db, "AAPL", 30)
    r = client.get("/api/securities/aapl/prices?days=10")
    bars = r.json()
    assert r.status_code == 200 and 9 <= len(bars) <= 11
    assert [b["ts"] for b in bars] == sorted(b["ts"] for b in bars)
    assert bars[-1]["close"] == 30.0 and set(bars[0]) == {
        "ts", "open", "high", "low", "close", "volume",
    }  # fmt: skip


def test_prices_are_thinned_but_keep_the_newest_bar(db):
    add_bars(db, "AAPL", 100)
    bars = client.get("/api/securities/AAPL/prices?days=400&max_points=10").json()
    assert 5 <= len(bars) <= 10 and bars[-1]["close"] == 100.0
    assert len(client.get("/api/securities/AAPL/prices?days=400").json()) == 100


def test_prices_and_scores_404_for_unknown_tickers(db):
    assert client.get("/api/securities/ZZZ/prices").status_code == 404
    assert client.get("/api/securities/ZZZ/scores").status_code == 404
    assert client.get("/api/securities/AAPL/prices?days=0").status_code == 422
    assert client.get("/api/securities/AAPL/prices?max_points=1").status_code == 422


def test_score_history_includes_flagged_gaps_and_signals(db):
    now = datetime.now(UTC)
    with session_scope(db) as s:
        sid = repo.get_security(s, "AAPL").id
        rows = []
        for i, (conv, blocked, abstained) in enumerate(
            [(60.0, False, False), (75.0, False, False), (None, False, True), (90.0, True, False)]
        ):
            score = signals_repo.insert_score(
                s, security_id=sid, as_of=now - timedelta(days=3 - i), conviction=conv,
                all_abstained=abstained, signal_blocked=blocked, explanation="", details={},
            )  # fmt: skip
            rows.append(score)
        signals_repo.insert_signal(
            s, security_id=sid, score_id=rows[1].id, kind="emerging", conviction=75.0,
            as_of=now - timedelta(days=2), reason="",
        )  # fmt: skip
    body = client.get("/api/securities/AAPL/scores").json()
    assert [p["conviction"] for p in body["scores"]] == [60.0, 75.0, None, 90.0]  # oldest first
    assert [(p["signal_blocked"], p["all_abstained"]) for p in body["scores"]] == [
        (False, False), (False, False), (False, True), (True, False),
    ]  # fmt: skip
    assert [(x["kind"], x["conviction"]) for x in body["signals"]] == [("emerging", 75.0)]
    assert client.get("/api/securities/MSFT/scores").json() == {"scores": [], "signals": []}


def test_503_without_a_database(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    for path in ("/api/securities", "/api/securities/AAPL/prices", "/api/securities/AAPL/scores"):
        assert client.get(path).status_code == 503
