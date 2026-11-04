"""One contract for the read side of the securities pages, run against the in-memory fakes and
the SQL adapter (SQLite, and Postgres when TEST_DATABASE_URL is set)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from db_fixtures import uow_factory  # noqa: F401 - pytest fixture

from fin_domain.market_data import PriceBar, SecurityUpdate
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import ScoreEntry, Signal

AAPL, MSFT, TLT, WEIRD, OLD = (Ticker(t) for t in ("AAPL", "MSFT", "TLT", "A-B", "OLD"))
T0 = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(uow_factory):  # noqa: F811
    with uow_factory() as uow:
        uow.securities.upsert(
            [
                SecurityUpdate(AAPL, {"name": "Apple Inc.", "sector": "Technology"}),
                SecurityUpdate(MSFT, {"name": "Microsoft Corp.", "sector": "Technology"}),
                SecurityUpdate(TLT, {"name": "iShares 20+ Year Treasury", "kind": "etf"}),
                SecurityUpdate(WEIRD, {"name": "100% Growth_Fund"}),
                SecurityUpdate(OLD, {"name": "Old Apple", "active": False}),
            ]
        )
    return uow_factory


def found(factory, **kw):
    with factory() as uow:
        return [str(s.ticker) for s in uow.security_search.search(**kw)]


# --- search ---------------------------------------------------------------------------------------


def test_search_without_a_query_lists_the_active_universe_by_ticker(seeded):
    listed = found(seeded)
    assert sorted(listed) == ["A-B", "AAPL", "MSFT", "TLT"]  # the inactive one is not listed
    # "A-B" sorts before or after "AAPL" depending on the database collation; the rest is fixed
    assert [t for t in listed if t != "A-B"] == ["AAPL", "MSFT", "TLT"]


def test_search_matches_a_ticker_prefix_or_a_name_fragment_case_insensitively(seeded):
    assert found(seeded, q="aa") == ["AAPL"]
    assert found(seeded, q="MICRO") == ["MSFT"]  # a name fragment, not a prefix
    assert found(seeded, q="  apple ") == ["AAPL"]  # trimmed; the inactive "Old Apple" is excluded
    assert found(seeded, q="zzz") == []


def test_search_filters_by_sector_and_kind(seeded):
    assert found(seeded, sector="Technology") == ["AAPL", "MSFT"]
    assert found(seeded, kind="etf") == ["TLT"]
    assert found(seeded, q="a", sector="Technology", kind="stock") == ["AAPL"]


def test_search_treats_wildcards_as_ordinary_characters(seeded):
    assert found(seeded, q="%") == ["A-B"]  # only "100% Growth_Fund" contains a percent sign
    assert found(seeded, q="_") == ["A-B"]  # and only it contains an underscore
    assert found(seeded, q="a%") == []  # not "a followed by anything"
    assert found(seeded, q="\\") == []


def test_search_is_limited(seeded):
    assert found(seeded, limit=2) == found(seeded)[:2]  # the first two in the database's order
    assert len(found(seeded, limit=2)) == 2


def test_search_returns_domain_securities_with_their_fields(seeded):
    with seeded() as uow:
        (apple,) = uow.security_search.search(q="aapl")
    assert (apple.name, apple.sector, apple.kind.value, apple.active) == (
        "Apple Inc.", "Technology", "stock", True,
    )  # fmt: skip


# --- price history --------------------------------------------------------------------------------


def test_bars_since_are_oldest_first_and_inclusive(seeded):
    with seeded() as uow:
        uow.prices.upsert(
            AAPL, [PriceBar(ts=T0 + timedelta(days=d), close=float(d)) for d in (3, 1, 2)]
        )
    with seeded() as uow:
        assert [b.close for b in uow.prices.bars_since(AAPL, T0 + timedelta(days=2))] == [2.0, 3.0]
        assert uow.prices.bars_since(AAPL, T0 + timedelta(days=9)) == []
        assert [b.close for b in uow.prices.bars_since(AAPL, T0)] == [1.0, 2.0, 3.0]


def test_bars_for_an_unknown_security_are_refused(seeded):
    with seeded() as uow, pytest.raises(NotFound):
        uow.prices.bars_since(Ticker("ZZZ"), T0)


# --- score history --------------------------------------------------------------------------------


def run(uow, ticker, conviction, minutes, *, blocked=False):
    return uow.scores.add(
        ScoreEntry(
            ticker,
            T0 + timedelta(days=minutes),
            conviction,
            conviction is None,
            blocked,
            "x",
            computed_at=T0 + timedelta(minutes=minutes),
        )  # fmt: skip
    )


def test_score_points_are_oldest_first_and_flag_blocked_and_empty_runs(seeded):
    with seeded() as uow:
        run(uow, AAPL, 60.0, 1)
        run(uow, AAPL, None, 2)
        run(uow, AAPL, 80.0, 3, blocked=True)
        run(uow, MSFT, 99.0, 1)  # another security: not mixed in
    with seeded() as uow:
        points = uow.score_history.scores(AAPL, T0, 100)
    assert [(p.conviction, p.signal_blocked, p.all_abstained) for p in points] == [
        (60.0, False, False), (None, False, True), (80.0, True, False),
    ]  # fmt: skip
    assert points[0].as_of == T0 + timedelta(days=1) and points[0].computed_at == T0 + timedelta(
        minutes=1
    )
    assert points[0].as_of.utcoffset() == timedelta(0)


def test_the_limit_keeps_the_newest_runs_and_since_drops_older_ones(seeded):
    with seeded() as uow:
        for i in range(1, 6):
            run(uow, AAPL, float(i), i)
    with seeded() as uow:
        assert [p.conviction for p in uow.score_history.scores(AAPL, T0, 3)] == [3.0, 4.0, 5.0]
        later = T0 + timedelta(minutes=4)
        assert [p.conviction for p in uow.score_history.scores(AAPL, later, 100)] == [4.0, 5.0]


def test_scores_and_signals_for_an_unknown_security_are_empty(seeded):
    with seeded() as uow:
        assert uow.score_history.scores(Ticker("ZZZ"), T0, 10) == []
        assert uow.score_history.signals(Ticker("ZZZ"), T0) == []


def test_signals_come_in_the_order_they_were_raised_from_a_given_time(seeded):
    with seeded() as uow:
        score = run(uow, AAPL, 80.0, 1)
        for kind, day in (("emerging", 1), ("sell", 5), ("buy", 3)):
            uow.signals.add(
                AAPL, score.id, Signal("AAPL", kind, 80.0, T0 + timedelta(days=day), "")
            )
        uow.signals.add(MSFT, score.id, Signal("MSFT", "buy", 80.0, T0, ""))
    with seeded() as uow:
        assert [s.kind for s in uow.score_history.signals(AAPL, T0)] == ["emerging", "sell", "buy"]
        assert [s.kind for s in uow.score_history.signals(AAPL, T0 + timedelta(days=3))] == [
            "sell", "buy",
        ]  # fmt: skip
