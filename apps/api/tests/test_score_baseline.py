"""GET /api/securities/{ticker}/score judges the signal against the stored baseline.

Without it a stock already at 85 would report "emerging" on every visit (no previous score), which
is not what the scheduled run would do. The graph itself is covered elsewhere; these tests pin what
the endpoint hands to it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import fin_api.main as main
from fin_core.db import migrate, repo, signals_repo
from fin_core.db.session import make_engine, session_scope

client = TestClient(main.app)
NOW = datetime.now(UTC)


@pytest.fixture
def db(monkeypatch, tmp_path):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    url = f"sqlite:///{tmp_path / 'b.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = make_engine(url)
    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "ACME"}])
    yield engine
    engine.dispose()
    main.dispose_engines()


@pytest.fixture
def calls(monkeypatch):
    """Record what the endpoint passes to score_security, then run the real thing."""
    seen: list[dict] = []
    real = main.score_security

    def spy(session, ticker, as_of, **kw):
        seen.append(kw)
        return real(session, ticker, as_of, **kw)

    monkeypatch.setattr(main, "score_security", spy)
    return seen


def store(engine, conviction, *, blocked=False, kind=None, age_days=1):
    with session_scope(engine) as s:
        sid = repo.get_security(s, "ACME").id
        score = signals_repo.insert_score(
            s, security_id=sid, as_of=NOW - timedelta(days=age_days), conviction=conviction,
            all_abstained=conviction is None, signal_blocked=blocked, explanation="", details={},
        )  # fmt: skip
        if kind:
            signals_repo.insert_signal(
                s, security_id=sid, score_id=score.id, kind=kind, conviction=conviction or 0,
                as_of=NOW - timedelta(days=age_days), reason="",
            )  # fmt: skip


def test_with_no_stored_score_there_is_no_baseline(db, calls):
    assert client.get("/api/securities/ACME/score").status_code == 200
    assert calls[0]["previous_conviction"] is None
    assert (calls[0]["buy_armed"], calls[0]["sell_armed"]) == (True, True)


def test_the_latest_actionable_stored_score_is_the_baseline(db, calls):
    store(db, 60.0, age_days=5)
    store(db, 85.0, age_days=3)
    store(db, 20.0, blocked=True, age_days=1)  # a blocked run never becomes the baseline
    assert client.get("/api/securities/ACME/score").status_code == 200
    assert calls[0]["previous_conviction"] == 85.0


def test_hysteresis_state_comes_from_the_stored_history(db, calls):
    store(db, 72.0, kind="buy", age_days=4)  # bought, and never dipped below the re-arm level
    store(db, 71.0, age_days=2)
    client.get("/api/securities/ACME/score")
    assert (calls[0]["buy_armed"], calls[0]["sell_armed"]) == (False, True)


def test_an_explicit_previous_conviction_or_as_of_skips_the_baseline(db, calls):
    store(db, 85.0)
    client.get("/api/securities/ACME/score", params={"previous_conviction": 10})
    client.get("/api/securities/ACME/score", params={"as_of": (NOW - timedelta(days=30)).date()})
    assert calls[0]["previous_conviction"] == 10.0
    assert calls[1]["previous_conviction"] is None  # a what-if as of a past date has no baseline


def test_an_unknown_ticker_is_still_a_404(db, calls):
    assert client.get("/api/securities/NOPE/score").status_code == 404
