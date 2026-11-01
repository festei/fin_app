"""Sandbox endpoints: portfolios, orders, positions, equity curve."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fin_api.main import app, dispose_engines
from fin_core.db import migrate, repo
from fin_core.db.models import Portfolio
from fin_core.db.session import make_engine, session_scope
from fin_core.sandbox import engine as sandbox

TOKEN = "test-token"
client = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
anonymous = TestClient(app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    yield
    dispose_engines()


@pytest.fixture
def db(monkeypatch, tmp_path):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    url = f"sqlite:///{tmp_path / 'sb.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = make_engine(url)
    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "AAA", "name": "Alpha"}, {"ticker": "BBB"}])
    yield engine
    engine.dispose()


def add_bars(engine, ticker, closes):
    """Daily closes ending today, oldest first."""
    today = datetime.now(UTC).date()
    n = len(closes)
    with session_scope(engine) as s:
        sid = repo.get_security(s, ticker).id
        repo.upsert_prices(
            s,
            sid,
            [
                {
                    "ts": datetime.combine(today - timedelta(days=n - i), datetime.min.time(), UTC)
                    .replace(hour=21),
                    "open": c, "high": c, "low": c, "close": c, "volume": 1,
                }
                for i, c in enumerate(closes)
            ],
        )  # fmt: skip


def new_portfolio(**kw):
    body = {"name": "Test", "mode": "manual", "cash": 100, **kw}
    r = client.post("/api/portfolios", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_list_portfolios(db):
    p = new_portfolio(name="My $100")
    assert (p["name"], p["mode"], p["cash"], p["total_value"], p["return_pct"]) == (
        "My $100", "manual", 100.0, 100.0, 0.0,
    )  # fmt: skip
    s = new_portfolio(mode="strategy", strategy={"position_pct": 0.5})
    assert (
        s["strategy_config"]["position_pct"] == 0.5 and s["strategy_config"]["max_positions"] == 5
    )
    assert [x["id"] for x in client.get("/api/portfolios").json()] == [p["id"], s["id"]]


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "mode": "bogus", "cash": 5},
        {"name": "x", "mode": "manual", "cash": 0},
        {"name": "", "mode": "manual", "cash": 5},
        {"name": "x", "mode": "strategy", "cash": 5, "strategy": {"nonsense": 1}},
        {"name": "x", "mode": "strategy", "cash": 5, "strategy": {"position_pct": 5}},
    ],
)
def test_invalid_portfolios_are_422(db, body):
    assert client.post("/api/portfolios", json=body).status_code == 422


def test_an_order_waits_for_a_later_close_then_the_step_fills_it(db):
    add_bars(db, "AAA", [10.0, 10.0])  # bars for yesterday and the day before: not "after today"
    pid = new_portfolio()["id"]
    r = client.post(
        f"/api/portfolios/{pid}/orders", json={"ticker": "aaa", "side": "buy", "notional": 50}
    )
    assert r.status_code == 201
    order = r.json()
    assert (order["ticker"], order["status"], order["notional"]) == ("AAA", "pending", 50.0)

    detail = client.get(f"/api/portfolios/{pid}").json()
    assert detail["positions"] == [] and detail["cash"] == 100.0  # nothing filled yet

    # a bar appears for tomorrow; the scheduled step fills the order at that close
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    with session_scope(db) as s:
        close = 12.5
        bar = dict(ts=tomorrow.replace(hour=21), open=close, high=close, low=close, close=close)
        repo.upsert_prices(s, repo.get_security(s, "AAA").id, [{**bar, "volume": 1}])
        sandbox.settle(s, s.get(Portfolio, pid), tomorrow.replace(hour=23))
    filled = client.get(f"/api/portfolios/{pid}/orders").json()[0]
    assert (filled["status"], filled["fill_price"], filled["fill_quantity"]) == (
        "filled",
        12.5,
        4.0,
    )
    pos = client.get(f"/api/portfolios/{pid}").json()["positions"][0]
    assert (pos["ticker"], pos["quantity"], pos["avg_cost"]) == ("AAA", 4.0, 12.5)
    assert pos["name"] == "Alpha"


def test_filters_orders_by_status_and_cancels_pending_ones(db):
    pid = new_portfolio()["id"]
    a = client.post(
        f"/api/portfolios/{pid}/orders", json={"ticker": "AAA", "side": "buy", "quantity": 1}
    ).json()
    client.post(
        f"/api/portfolios/{pid}/orders", json={"ticker": "BBB", "side": "buy", "quantity": 1}
    )
    assert client.delete(f"/api/portfolios/{pid}/orders/{a['id']}").json() == {
        "id": a["id"], "cancelled": True,
    }  # fmt: skip
    assert (
        client.delete(f"/api/portfolios/{pid}/orders/{a['id']}").status_code == 404
    )  # not pending
    assert [
        o["ticker"] for o in client.get(f"/api/portfolios/{pid}/orders?status=pending").json()
    ] == ["BBB"]
    assert [
        o["status"] for o in client.get(f"/api/portfolios/{pid}/orders?status=cancelled").json()
    ] == ["cancelled"]


def test_order_errors(db):
    pid = new_portfolio()["id"]
    strategy = new_portfolio(mode="strategy")["id"]
    post = lambda p, **b: client.post(f"/api/portfolios/{p}/orders", json=b)  # noqa: E731
    assert post(pid, ticker="ZZZ", side="buy", quantity=1).status_code == 404
    assert post(999, ticker="AAA", side="buy", quantity=1).status_code == 404
    assert post(pid, ticker="AAA", side="buy").status_code == 422
    assert post(pid, ticker="AAA", side="buy", quantity=1, notional=5).status_code == 422
    assert post(pid, ticker="AAA", side="hold", quantity=1).status_code == 422
    assert post(strategy, ticker="AAA", side="buy", quantity=1).status_code == 422
    assert client.get("/api/portfolios/999").status_code == 404
    assert client.get("/api/portfolios/999/orders").status_code == 404
    assert client.get("/api/portfolios/999/history").status_code == 404


def test_history_returns_the_equity_curve(db):
    pid = new_portfolio()["id"]
    with session_scope(db) as s:
        p = s.get(Portfolio, pid)
        sandbox.snapshot(s, p, datetime(2025, 3, 10, 22, tzinfo=UTC))
        p.cash = 120
        sandbox.snapshot(s, p, datetime(2025, 3, 11, 22, tzinfo=UTC))
    assert client.get(f"/api/portfolios/{pid}/history").json() == [
        {"as_of": "2025-03-10", "cash": 100.0, "positions_value": 0.0, "total_value": 100.0},
        {"as_of": "2025-03-11", "cash": 120.0, "positions_value": 0.0, "total_value": 120.0},
    ]


def test_position_pnl_uses_the_latest_close(db):
    pid = new_portfolio(cash=1000)["id"]
    add_bars(db, "AAA", [10.0, 15.0])
    with session_scope(db) as s:
        p = s.get(Portfolio, pid)
        p.cash = 900
        from fin_core.db.models import SandboxPosition

        s.add(SandboxPosition(portfolio_id=pid, security_id=repo.get_security(s, "AAA").id,
                              quantity=10, avg_cost=10))  # fmt: skip
    d = client.get(f"/api/portfolios/{pid}").json()
    pos = d["positions"][0]
    assert (pos["price"], pos["market_value"], pos["pnl"], pos["pnl_pct"]) == (
        15.0,
        150.0,
        50.0,
        50.0,
    )
    assert (d["positions_value"], d["total_value"], d["return_pct"]) == (150.0, 1050.0, 5.0)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/portfolios", {"name": "x", "mode": "manual", "cash": 5}),
        ("post", "/api/portfolios/1/orders", {"ticker": "AAA", "side": "buy", "quantity": 1}),
        ("delete", "/api/portfolios/1/orders/1", None),
    ],
)
def test_writes_need_the_token_and_reads_do_not(db, method, path, body):
    call = getattr(anonymous, method)
    assert (call(path, json=body) if body else call(path)).status_code == 401
    assert anonymous.get("/api/portfolios").status_code == 200


def test_503_without_a_database(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    assert client.get("/api/portfolios").status_code == 503
    assert date  # keep the import used
