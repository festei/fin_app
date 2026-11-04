"""One contract for the Signals & Alerts repositories and queries, run against the in-memory
fakes and the SQL adapter (SQLite, and Postgres when TEST_DATABASE_URL is set)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from db_fixtures import uow_factory  # noqa: F401 - pytest fixture

from fin_application.errors import ConcurrencyConflict
from fin_domain.market_data import SecurityUpdate
from fin_domain.scoring import Criterion, ScoreResult
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import (
    Alert,
    ChannelState,
    DeliveryStatus,
    MetricsSummary,
    ScoreDetails,
    ScoreEntry,
    Signal,
)
from fin_persistence.score_codec import details_from_json, details_to_json

AAA, BBB, OLD = Ticker("AAA"), Ticker("BBB"), Ticker("OLD")
T0 = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(uow_factory):  # noqa: F811
    with uow_factory() as uow:
        uow.securities.upsert(
            [
                SecurityUpdate(AAA, {"name": "Alpha", "sector": "Tech"}),
                SecurityUpdate(BBB, {"name": "Beta"}),
                SecurityUpdate(OLD, {"active": False}),
            ]
        )
    return uow_factory


DETAILS = ScoreDetails(
    philosophies={
        "graham": ScoreResult("graham", 57.0, [Criterion("pe", True, 10.0, 15.0, "ok")]),
        "buffett": ScoreResult("buffett", 0.0, explanation="too little history", evaluated=False),
    },
    guardrails={"piotroski": ScoreResult("piotroski", 60.0)},
    guardrail_reasons=("weak",),
    guardrail_notes=("note",),
    warnings=("w",),
    signal_blockers=("b",),
    metrics=MetricsSummary(25.0, date(2025, 3, 7), 10.0, 11.0, 2.5, date(2024, 12, 31)),
)


def entry(ticker, conviction, *, minutes=0, blocked=False, details=None):
    return ScoreEntry(
        ticker=ticker,
        as_of=T0,
        conviction=conviction,
        all_abstained=conviction is None,
        signal_blocked=blocked,
        explanation="why",
        details=details or ScoreDetails(),
        computed_at=T0 + timedelta(minutes=minutes),
    )


def store_scores(factory, ticker, *convictions, **kw):
    ids = []
    with factory() as uow:
        for i, c in enumerate(convictions):
            ids.append(uow.scores.add(entry(ticker, c, minutes=i, **kw)).id)
    return ids


# --- the stored JSON shape ------------------------------------------------------------------------


def test_the_stored_json_keeps_the_shape_it_always_had():
    j = details_to_json(DETAILS)
    assert list(j) == [
        "philosophies", "guardrails", "guardrail_reasons", "guardrail_notes", "warnings",
        "signal_blockers", "metrics",
    ]  # fmt: skip
    assert list(j["philosophies"]["graham"]) == [
        "philosophy", "score", "criteria", "explanation", "evidence", "impl", "evaluated",
    ]  # fmt: skip
    assert j["philosophies"]["graham"]["criteria"] == [
        {"name": "pe", "passed": True, "actual": 10.0, "threshold": 15.0, "detail": "ok"}
    ]
    assert j["metrics"] == {
        "price": 25.0, "price_date": "2025-03-07", "pe": 10.0, "pe_avg3": 11.0, "pb": 2.5,
        "latest_fiscal_year_end": "2024-12-31",
    }  # fmt: skip


def test_details_survive_a_round_trip():
    assert details_from_json(details_to_json(DETAILS)) == DETAILS


@pytest.mark.parametrize("raw", [None, {}, {"metrics": {}}, {"philosophies": {}}])
def test_missing_or_partial_details_read_as_nothing_recorded(raw):
    assert details_from_json(raw) == ScoreDetails()


def test_a_hand_written_partial_row_still_reads():
    raw = {"philosophies": {"graham": {"score": 80.0}}, "metrics": {"price": 9.5}}
    d = details_from_json(raw)
    assert d.philosophies["graham"].score == 80.0 and d.philosophies["graham"].evaluated
    assert d.metrics.price == 9.5 and d.metrics.price_date is None


# --- scores ---------------------------------------------------------------------------------------


def test_a_stored_score_comes_back_with_its_details(seeded):
    with seeded() as uow:
        added = uow.scores.add(entry(AAA, 78.5, details=DETAILS))
    assert added.id is not None and added.computed_at == T0
    with seeded() as uow:
        got = uow.scores.get(added.id)
    assert got.details == DETAILS and got.conviction == 78.5 and str(got.ticker) == "AAA"
    assert got.as_of == T0 and got.as_of.utcoffset() == timedelta(0)  # aware, even from SQLite
    with seeded() as uow:
        assert uow.scores.get(424242) is None


def test_scores_for_an_unknown_security_are_refused_and_reads_are_empty(seeded):
    with seeded() as uow, pytest.raises(NotFound):
        uow.scores.add(entry(Ticker("ZZZ"), 50.0))
    with seeded() as uow:
        zzz = Ticker("ZZZ")
        assert uow.scores.latest(zzz) is None and uow.scores.latest_actionable(zzz) is None
        assert uow.scores.convictions_since_last_signal(zzz, ["buy"]) is None


def test_latest_is_the_newest_run_and_latest_actionable_skips_blocked_and_null_ones(seeded):
    store_scores(seeded, AAA, 55.0)
    with seeded() as uow:
        uow.scores.add(entry(AAA, None, minutes=5))  # nothing evaluated
        uow.scores.add(entry(AAA, 99.0, minutes=6, blocked=True))  # stale data
    with seeded() as uow:
        assert uow.scores.latest(AAA).conviction == 99.0
        assert uow.scores.latest_actionable(AAA).conviction == 55.0


def test_a_run_with_the_same_time_is_ordered_by_when_it_was_stored(seeded):
    with seeded() as uow:
        uow.scores.add(entry(AAA, 10.0))
        uow.scores.add(entry(AAA, 20.0))
    with seeded() as uow:
        assert uow.scores.latest(AAA).conviction == 20.0


def test_convictions_since_the_last_signal_of_a_kind(seeded):
    first, second, third, fourth = store_scores(seeded, AAA, 60.0, 80.0, 72.0, 64.0)
    with seeded() as uow:
        assert uow.scores.convictions_since_last_signal(AAA, ["buy", "emerging"]) is None
        uow.signals.add(AAA, second, Signal("AAA", "emerging", 80.0, T0, ""))
    with seeded() as uow:
        assert uow.scores.convictions_since_last_signal(AAA, ["buy", "emerging"]) == [72.0, 64.0]
        assert uow.scores.convictions_since_last_signal(AAA, ["sell"]) is None  # another kind
    assert first < second < third < fourth


def test_blocked_and_null_runs_do_not_count_towards_re_arming(seeded):
    (anchor,) = store_scores(seeded, AAA, 80.0)
    with seeded() as uow:
        uow.signals.add(AAA, anchor, Signal("AAA", "emerging", 80.0, T0, ""))
        uow.scores.add(entry(AAA, 60.0, minutes=1, blocked=True))
        uow.scores.add(entry(AAA, None, minutes=2))
        uow.scores.add(entry(AAA, 66.0, minutes=3))
    with seeded() as uow:
        assert uow.scores.convictions_since_last_signal(AAA, ["emerging"]) == [66.0]


# --- signals --------------------------------------------------------------------------------------


def test_a_signal_is_stored_pending_and_found_again(seeded):
    (score_id,) = store_scores(seeded, AAA, 80.0)
    with seeded() as uow:
        stored = uow.signals.add(AAA, score_id, Signal("AAA", "emerging", 80.0, T0, "why"))
    assert stored.id is not None and stored.is_pending and stored.created_at is not None
    with seeded() as uow:
        got = uow.signals.get(stored.id)
        assert uow.signals.get(999999) is None
    assert (got.score_id, got.signal.kind, got.signal.reason) == (score_id, "emerging", "why")
    assert got.signal.as_of == T0 and got.signal.ticker == "AAA"


def test_processing_a_signal_removes_it_from_the_owed_list(seeded):
    (score_id,) = store_scores(seeded, AAA, 80.0)
    with seeded() as uow:
        a = uow.signals.add(AAA, score_id, Signal("AAA", "emerging", 80.0, T0, ""))
        b = uow.signals.add(AAA, score_id, Signal("AAA", "sell", 40.0, T0, ""))
    with seeded() as uow:
        assert uow.signals.pending_ids() == [a.id, b.id]
        stored = uow.signals.get(a.id)
        stored.mark_processed(T0, "r" * 300)
        uow.signals.save(stored)
    with seeded() as uow:
        assert uow.signals.pending_ids() == [b.id]
        done = uow.signals.get(a.id)
    assert done.processed_at == T0 and done.suppressed_reason == "r" * 200


def test_saving_a_signal_that_was_never_added_fails(seeded):
    (score_id,) = store_scores(seeded, AAA, 80.0)
    with seeded() as uow:
        ghost = uow.signals.add(AAA, score_id, Signal("AAA", "buy", 80.0, T0, ""))
        ghost.id = 777
        with pytest.raises(LookupError):
            uow.signals.save(ghost)


# --- alerts and deliveries ------------------------------------------------------------------------


def make_alert(signal_id, ticker=AAA, kind="buy", conviction=80.0, created=T0):
    return Alert(
        ticker, kind, "high", conviction, "title", "body\nUse /why AAA", created, signal_id
    )


def stored_alert(factory, channels=("in_app", "telegram"), ticker=AAA, kind="buy", **kw):
    """An alert, behind a real signal on a real score (the schema requires both)."""
    with factory() as uow:
        score = uow.scores.add(entry(ticker, 80.0))
        signal = uow.signals.add(ticker, score.id, Signal(str(ticker), kind, 80.0, T0, ""))
        alert = make_alert(signal.id, ticker, kind, **kw)
        alert.open_deliveries_for(channels, muted=set(), now=T0)
        return uow.alerts.add(alert)


def test_an_alert_is_stored_with_its_deliveries_and_ids(seeded):
    alert = stored_alert(seeded)
    assert alert.id is not None and all(d.id is not None for d in alert.deliveries)
    with seeded() as uow:
        (got,) = uow.alert_feed.page()
    assert (got.id, str(got.ticker), got.kind, got.body) == (alert.id, "AAA", "buy", alert.body)
    by = {d.channel: d for d in got.deliveries}
    assert by["in_app"].status is DeliveryStatus.SENT and by["in_app"].attempts == 1
    assert by["telegram"].status is DeliveryStatus.PENDING and by["telegram"].attempts == 0
    assert got.created_at == T0 and got.created_at.utcoffset() == timedelta(0)


def test_an_alert_is_found_by_its_signal_and_the_last_one_by_kind(seeded):
    first = stored_alert(seeded, conviction=80.0, created=T0)
    stored_alert(seeded, conviction=91.0, created=T0 + timedelta(hours=1))
    stored_alert(seeded, kind="sell", conviction=40.0, created=T0 + timedelta(hours=2))
    with seeded() as uow:
        assert uow.alerts.get_by_signal(first.signal_id).conviction == 80.0
        assert uow.alerts.get_by_signal(999999) is None
        last = uow.alerts.last(AAA, "buy")
        assert (last.conviction, last.created_at) == (91.0, T0 + timedelta(hours=1))
        assert uow.alerts.last(AAA, "emerging") is None and uow.alerts.last(BBB, "buy") is None


def test_open_deliveries_are_the_owed_ones_with_attempts_left(seeded):
    alert = stored_alert(seeded)
    with seeded() as uow:
        rows = uow.alerts.open_deliveries("telegram", 6)
        assert [(d.channel, a.id) for d, a in rows] == [("telegram", alert.id)]
        assert rows[0][1].body == alert.body and str(rows[0][1].ticker) == "AAA"
        assert uow.alerts.open_deliveries("in_app", 6) == []  # already sent
        assert uow.alerts.open_deliveries("telegram", 0) == []  # no attempts allowed


def test_saving_a_delivery_stores_its_progress(seeded):
    stored_alert(seeded)
    with seeded() as uow:
        d = uow.alerts.open_deliveries("telegram", 6)[0][0]
        d.claim(T0)
        d.mark_failed("x" * 900)
        uow.alerts.save_delivery(d)
    with seeded() as uow:
        d = uow.alerts.open_deliveries("telegram", 6)[0][0]
    assert (d.status, d.attempts, d.last_attempt_at, len(d.error)) == (
        DeliveryStatus.FAILED, 1, T0, 500,
    )  # fmt: skip
    with seeded() as uow:
        d.claim(T0 + timedelta(minutes=3))
        d.mark_sent(T0 + timedelta(minutes=3))
        uow.alerts.save_delivery(d)
    with seeded() as uow:
        assert uow.alerts.open_deliveries("telegram", 6) == []
        (got,) = uow.alert_feed.page()
    sent = got.delivery("telegram")
    assert (sent.status, sent.error, sent.attempts) == (DeliveryStatus.SENT, None, 2)
    assert sent.delivered_at == T0 + timedelta(minutes=3)


def test_a_claim_is_a_compare_and_set_on_the_attempts(seeded):
    stored_alert(seeded)
    with seeded() as uow:
        stale = uow.alerts.open_deliveries("telegram", 6)[0][0]
    with seeded() as uow:
        mine = uow.alerts.open_deliveries("telegram", 6)[0][0]
        mine.claim(T0)
        uow.alerts.save_delivery(mine, expected_attempts=0)  # wins
    with seeded() as uow:
        stale.claim(T0)
        with pytest.raises(ConcurrencyConflict):
            uow.alerts.save_delivery(stale, expected_attempts=0)  # same expectation, now stale
    with seeded() as uow:
        assert uow.alerts.open_deliveries("telegram", 6)[0][0].attempts == 1


def test_a_finished_delivery_cannot_be_claimed_through_the_store_either(seeded):
    stored_alert(seeded)
    with seeded() as uow:
        d = uow.alerts.open_deliveries("telegram", 6)[0][0]
        d.skip("channel muted")
        uow.alerts.save_delivery(d)
    with seeded() as uow:
        again = uow.alerts.open_deliveries("telegram", 6)
        assert again == []
        (alert,) = uow.alert_feed.page()
        stale = alert.delivery("telegram")
        stale.status, stale.attempts = DeliveryStatus.PENDING, 1
        with pytest.raises(ConcurrencyConflict):
            uow.alerts.save_delivery(stale, expected_attempts=0)


def test_reading_an_alert_keeps_the_first_time(seeded):
    alert = stored_alert(seeded)
    with seeded() as uow:
        assert uow.alerts.mark_read(alert.id, T0) is True
        assert uow.alerts.mark_read(alert.id, T0 + timedelta(hours=1)) is True
        assert uow.alerts.mark_read(999999, T0) is False
    with seeded() as uow:
        assert uow.alert_feed.page()[0].read_at == T0


def test_the_feed_is_newest_first_with_a_cursor_and_an_unread_filter(seeded):
    ids = [stored_alert(seeded, created=T0 + timedelta(minutes=i)).id for i in range(3)]
    with seeded() as uow:
        assert [a.id for a in uow.alert_feed.page(limit=2)] == [ids[2], ids[1]]
        assert [a.id for a in uow.alert_feed.page(limit=2, before_id=ids[1])] == [ids[0]]
        uow.alerts.mark_read(ids[2], T0)
    with seeded() as uow:
        assert [a.id for a in uow.alert_feed.page(unread_only=True)] == [ids[1], ids[0]]
        assert uow.alert_feed.page(before_id=ids[0]) == []


# --- watchlist, channels, cursor ------------------------------------------------------------------


def test_the_watchlist_is_idempotent_and_ordered(seeded):
    with seeded() as uow:
        assert uow.watchlist.add(BBB) is True and uow.watchlist.add(BBB) is False
        uow.watchlist.add(AAA)
    with seeded() as uow:
        assert uow.watchlist.tickers() == ["AAA", "BBB"]
        assert uow.watchlist.contains(AAA) and not uow.watchlist.contains(OLD)
        assert uow.watchlist.remove(AAA) is True and uow.watchlist.remove(AAA) is False
        assert not uow.watchlist.contains(Ticker("ZZZ")) and not uow.watchlist.remove(Ticker("ZZZ"))
    with seeded() as uow:
        assert uow.watchlist.tickers() == ["BBB"]


def test_watching_an_unknown_security_is_refused(seeded):
    with seeded() as uow, pytest.raises(NotFound):
        uow.watchlist.add(Ticker("ZZZ"))


def test_a_channel_nobody_touched_is_simply_not_muted(seeded):
    with seeded() as uow:
        state = uow.channels.get("telegram")
    assert state.channel == "telegram" and state.muted_until is None


def test_muting_round_trips_and_unmuting_clears_it(seeded):
    until = T0 + timedelta(hours=5)
    with seeded() as uow:
        state = uow.channels.get("telegram")
        state.mute_until(until)
        uow.channels.save(state)
    with seeded() as uow:
        got = uow.channels.get("telegram")
        assert got.muted_until == until and got.muted_until.utcoffset() == timedelta(0)
        assert got.is_muted(T0)
        got.unmute()
        uow.channels.save(got)
    with seeded() as uow:
        assert uow.channels.get("telegram") == ChannelState("telegram", None)


def test_the_bot_cursor_and_the_mute_do_not_disturb_each_other(seeded):
    until = T0 + timedelta(hours=5)
    with seeded() as uow:
        assert uow.cursors.load("telegram") is None
        uow.cursors.save("telegram", 41)
        uow.channels.save(ChannelState("telegram", until))
        uow.cursors.save("telegram", 42)
    with seeded() as uow:
        assert uow.cursors.load("telegram") == 42
        assert uow.channels.get("telegram").muted_until == until


# --- opportunities --------------------------------------------------------------------------------


def test_opportunities_rank_the_latest_actionable_score_of_each_active_security(seeded):
    store_scores(seeded, AAA, 40.0, 70.0)  # the latest wins
    store_scores(seeded, BBB, 90.0, details=DETAILS)
    store_scores(seeded, OLD, 99.0)  # inactive: not listed
    with seeded() as uow:
        uow.scores.add(entry(AAA, 99.0, minutes=9, blocked=True))  # stale: ignored
        uow.scores.add(entry(BBB, None, minutes=9))  # nothing evaluated: ignored
    with seeded() as uow:
        rows = uow.opportunities.top()
    assert [(str(r.ticker), r.conviction) for r in rows] == [("BBB", 90.0), ("AAA", 70.0)]
    assert rows[1].name == "Alpha" and rows[1].sector == "Tech" and rows[0].name == "Beta"
    assert rows[0].details == DETAILS and rows[0].computed_at == T0
    with seeded() as uow:
        assert [str(r.ticker) for r in uow.opportunities.top(limit=1)] == ["BBB"]


def test_opportunities_can_drop_scores_older_than_a_cutoff(seeded):
    store_scores(seeded, AAA, 70.0)
    with seeded() as uow:
        uow.scores.add(entry(BBB, 80.0, minutes=60 * 24 * 11))
    with seeded() as uow:
        fresh = uow.opportunities.top(newer_than=T0 + timedelta(days=1))
    assert [str(r.ticker) for r in fresh] == ["BBB"]


def test_equal_convictions_are_ordered_by_ticker(seeded):
    store_scores(seeded, BBB, 70.0)
    store_scores(seeded, AAA, 70.0)
    with seeded() as uow:
        assert [str(r.ticker) for r in uow.opportunities.top()] == ["AAA", "BBB"]
