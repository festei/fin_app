"""Alert feed, opportunities, watchlist and channel endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fin_api.main import app, dispose_engines
from fin_core.db import migrate, repo, signals_repo
from fin_core.db.models import AlertDelivery
from fin_core.db.session import make_engine, session_scope

client = TestClient(app)
NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _close_cached_engines():
    yield
    dispose_engines()


def clear_db_env(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def db(monkeypatch, tmp_path):
    clear_db_env(monkeypatch)
    url = f"sqlite:///{tmp_path / 'feed.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = make_engine(url)
    with session_scope(engine) as s:
        repo.upsert_securities(
            s,
            [
                {"ticker": "AAA", "name": "Alpha", "sector": "Technology"},
                {"ticker": "BBB", "name": "Beta"},
                {"ticker": "STALE"},
            ],
        )
    yield engine
    engine.dispose()


def add_score(engine, ticker, conviction, *, blocked=False, price=10.0):
    with session_scope(engine) as s:
        sid = repo.get_security(s, ticker).id
        details = {
            "philosophies": {
                "graham": {"score": 60.0, "evaluated": True},
                "buffett": {"score": 0.0, "evaluated": False},
                "lynch": {"score": 100.0, "evaluated": True},
            },
            "metrics": {"price": price, "price_date": "2025-03-03"},
        }
        score = signals_repo.insert_score(
            s, security_id=sid, as_of=NOW, conviction=conviction, all_abstained=False,
            signal_blocked=blocked, explanation="x", details=details,
        )  # fmt: skip
        return sid, score.id


def add_alert(engine, ticker, kind, minutes, *, channels=("in_app",)):
    sid, score_id = add_score(engine, ticker, 80.0)
    with session_scope(engine) as s:
        signal = signals_repo.insert_signal(
            s, security_id=sid, score_id=score_id, kind=kind, conviction=80.0, as_of=NOW, reason=""
        )
        alert = signals_repo.insert_alert(
            s,
            security_id=sid,
            signal_id=signal.id,
            kind=kind,
            severity="high",
            conviction=80.0,
            title=f"{ticker}: {kind.upper()}",
            body="body",
            created_at=NOW + timedelta(minutes=minutes),
        )
        for ch in channels:
            s.add(AlertDelivery(alert_id=alert.id, channel=ch, status="sent", attempts=1))
        return alert.id


# --- alerts -------------------------------------------------------------------------------------


def test_alert_feed_newest_first_with_deliveries(db):
    first = add_alert(db, "AAA", "emerging", 0)
    second = add_alert(db, "BBB", "buy", 5, channels=("in_app", "telegram"))
    data = client.get("/api/alerts").json()
    assert [i["id"] for i in data["items"]] == [second, first] and data["next_before_id"] is None
    top = data["items"][0]
    assert (top["ticker"], top["kind"], top["severity"], top["read"]) == (
        "BBB",
        "buy",
        "high",
        False,
    )
    assert top["deliveries"] == {"in_app": "sent", "telegram": "sent"}
    assert top["title"] == "BBB: BUY" and top["created_at"].startswith("2025-03-10T12:05")


def test_alert_feed_pages_with_a_cursor_and_filters_unread(db):
    ids = [add_alert(db, "AAA", "emerging", m) for m in (0, 1, 2)]
    page1 = client.get("/api/alerts", params={"limit": 2}).json()
    assert [i["id"] for i in page1["items"]] == ids[::-1][:2]
    assert page1["next_before_id"] == ids[1]
    page2 = client.get(
        "/api/alerts", params={"limit": 2, "before_id": page1["next_before_id"]}
    ).json()
    assert [i["id"] for i in page2["items"]] == [ids[0]] and page2["next_before_id"] is None

    assert client.post(f"/api/alerts/{ids[2]}/read").json() == {"id": ids[2], "read": True}
    unread = client.get("/api/alerts", params={"unread_only": True}).json()["items"]
    assert [i["id"] for i in unread] == [ids[1], ids[0]]
    read_item = next(i for i in client.get("/api/alerts").json()["items"] if i["id"] == ids[2])
    assert read_item["read"] is True


def test_marking_an_unknown_alert_is_a_404(db):
    assert client.post("/api/alerts/999/read").status_code == 404


def test_feed_limits_are_validated(db):
    assert client.get("/api/alerts", params={"limit": 0}).status_code == 422
    assert client.get("/api/alerts", params={"limit": 500}).status_code == 422


# --- opportunities ------------------------------------------------------------------------------


def test_opportunities_rank_by_latest_actionable_conviction(db):
    add_score(db, "AAA", 70.0)
    add_score(db, "AAA", 85.0)  # the newest actionable score wins
    add_score(db, "BBB", 90.0, price=22.5)
    add_score(db, "STALE", 99.0, blocked=True)  # computed on stale data: not listed
    rows = client.get("/api/opportunities").json()
    assert [r["ticker"] for r in rows] == ["BBB", "AAA"]
    assert rows[1]["conviction"] == 85.0 and rows[1]["name"] == "Alpha"
    assert rows[1]["sector"] == "Technology"
    assert rows[0]["philosophies"] == {"graham": 60.0, "lynch": 100.0}  # buffett abstained
    assert rows[0]["price"] == 22.5 and rows[0]["price_date"] == "2025-03-03"
    assert [r["ticker"] for r in client.get("/api/opportunities", params={"limit": 1}).json()] == [
        "BBB"
    ]


# --- watchlist ----------------------------------------------------------------------------------


def test_watchlist_add_list_remove_is_idempotent(db):
    assert client.get("/api/watchlist").json() == {"tickers": []}
    assert client.put("/api/watchlist/aaa").json() == {"ticker": "AAA", "added": True}
    assert client.put("/api/watchlist/AAA").json() == {"ticker": "AAA", "added": False}
    client.put("/api/watchlist/BBB")
    assert client.get("/api/watchlist").json() == {"tickers": ["AAA", "BBB"]}
    assert client.delete("/api/watchlist/AAA").json() == {"ticker": "AAA", "removed": True}
    assert client.delete("/api/watchlist/AAA").json() == {"ticker": "AAA", "removed": False}
    assert client.get("/api/watchlist").json() == {"tickers": ["BBB"]}


def test_watchlist_rejects_unknown_tickers(db):
    assert client.put("/api/watchlist/ZZZ").status_code == 404
    assert client.delete("/api/watchlist/ZZZ").status_code == 404


# --- channels -----------------------------------------------------------------------------------


def test_channels_follow_the_feature_flag_and_show_mutes(db, monkeypatch):
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    assert client.get("/api/channels").json() == [
        {"name": "in_app", "enabled": True, "muted_until": None},
        {"name": "telegram", "enabled": False, "muted_until": None},
    ]
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    with session_scope(db) as s:
        signals_repo.set_muted_until(s, "telegram", NOW)
    telegram = client.get("/api/channels").json()[1]
    assert telegram["enabled"] is True and telegram["muted_until"].startswith("2025-03-10T12:00")


def test_channels_work_without_a_database(monkeypatch):
    clear_db_env(monkeypatch)
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    data = client.get("/api/channels").json()
    assert [(c["name"], c["enabled"]) for c in data] == [("in_app", True), ("telegram", False)]


# --- no database --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/alerts"),
        ("post", "/api/alerts/1/read"),
        ("get", "/api/opportunities"),
        ("get", "/api/watchlist"),
        ("put", "/api/watchlist/AAA"),
        ("delete", "/api/watchlist/AAA"),
    ],
)
def test_endpoints_return_503_without_a_database(monkeypatch, method, path):
    clear_db_env(monkeypatch)
    r = getattr(client, method)(path)
    assert r.status_code == 503 and "not configured" in r.json()["detail"]


def test_an_unreachable_database_is_503_without_connection_details(monkeypatch, tmp_path):
    clear_db_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'missing-dir' / 'x.db'}")
    r = client.get("/api/alerts")
    assert r.status_code == 503 and "database unavailable" in r.json()["detail"]
    assert "missing-dir" not in r.json()["detail"]
