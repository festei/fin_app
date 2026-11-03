"""Signals become alerts under the policy, and alerts are delivered, retried and muted."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from scenario import NOW, World

from fin_application.watchlist import AlertFeed, ChannelService, WatchlistService
from fin_domain.shared import NotFound, Ticker
from fin_domain.signals import AlertPolicy, DeliveryStatus, Signal
from fin_testkit import FakeChannel


def manual_signal(w: World, ticker: str, kind: str, conviction: float) -> int:
    """A signal tied to the security's latest score, for policy tests that need control."""
    symbol = Ticker(ticker)
    with w.uow_factory() as uow:
        score = uow.scores.latest(symbol)
        return uow.signals.add(symbol, score.id, Signal(ticker, kind, conviction, NOW, "")).id


def deliveries(w: World, channel: str | None = None):
    rows = [
        (a.id, d.channel, d.status.value, d.attempts)
        for a in sorted(w.store.alerts.values(), key=lambda a: a.id)
        for d in a.deliveries
        if channel is None or d.channel == channel
    ]
    return rows


def alerts(w: World):
    return [(a.id, a.kind, a.severity) for a in sorted(w.store.alerts.values(), key=lambda a: a.id)]


# --- creating alerts ----------------------------------------------------------------------------


def test_a_signal_becomes_one_alert_with_an_in_app_delivery():
    w = World(conviction=80.0)
    w.seed("AAA")
    ids = w.new_signal_ids()
    (outcome,) = w.process.execute(ids, NOW)
    assert outcome.alert_id is not None and outcome.suppressed is None
    assert alerts(w) == [(outcome.alert_id, "emerging", "normal")]
    assert deliveries(w) == [(outcome.alert_id, "in_app", "sent", 1)]

    again = w.process.execute(ids, NOW)  # the same signal, e.g. a re-run
    assert again[0].alert_id == outcome.alert_id
    assert len(alerts(w)) == 1 and len(deliveries(w)) == 1  # nothing duplicated


def test_the_alert_text_is_composed_from_the_stored_score():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.process.execute(w.new_signal_ids(), NOW)
    (alert,) = w.store.alerts.values()
    assert alert.title.startswith("AAA: EMERGING (conviction 80)")
    assert "Scores: stub 80" in alert.body and "Use /why AAA" in alert.body
    assert alert.created_at == NOW and alert.signal_id is not None


def test_sell_alerts_only_for_followed_stocks():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BBB")
    w.process.execute(w.new_signal_ids(), NOW)  # both emerging
    WatchlistService(w.uow_factory).add("BBB")

    w.engine.conviction = 40.0
    sells = w.new_signal_ids(as_of=date(2025, 3, 12))
    assert len(sells) == 2  # both stocks fall below the sell line
    out = w.process.execute(sells, NOW + timedelta(hours=30))
    by_ticker = {o.ticker: o for o in out}
    assert by_ticker["AAA"].alert_id is None and "watchlist" in by_ticker["AAA"].suppressed
    assert by_ticker["BBB"].alert_id is not None
    sell_alerts = [a for a in alerts(w) if a[1] == "sell"]
    assert len(sell_alerts) == 1 and sell_alerts[0][2] == "high"  # followed => high severity


def test_cooldown_in_the_pipeline_and_material_change_realert():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.score()
    first = manual_signal(w, "AAA", "buy", 80.0)
    assert w.process.execute([first], NOW)[0].alert_id
    quiet = manual_signal(w, "AAA", "buy", 83.0)
    out = w.process.execute([quiet], NOW + timedelta(hours=2))
    assert out[0].alert_id is None and "cooldown" in out[0].suppressed
    jump = manual_signal(w, "AAA", "buy", 95.0)
    assert w.process.execute([jump], NOW + timedelta(hours=3))[0].alert_id
    later = manual_signal(w, "AAA", "buy", 96.0)
    assert w.process.execute([later], NOW + timedelta(hours=30))[0].alert_id
    assert len(w.store.alerts) == 3


def test_a_suppressed_signal_is_marked_processed_with_its_reason():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BBB")
    w.process.execute(w.new_signal_ids(), NOW)
    w.engine.conviction = 40.0
    w.process.execute(w.new_signal_ids(as_of=date(2025, 3, 12)), NOW)
    sells = [s for s in w.store.signals.values() if s.signal.kind == "sell"]
    assert sells and all(not s.is_pending and "watchlist" in s.suppressed_reason for s in sells)


# --- delivery -------------------------------------------------------------------------------------


def test_a_failing_channel_is_recorded_and_never_blocks_the_others():
    w = World(conviction=80.0)
    w.seed("AAA")
    broken, healthy = FakeChannel("broken", fail=True), FakeChannel("healthy")
    w.channels = [w.in_app, broken, healthy]
    (outcome,) = w.process.execute(w.new_signal_ids(), NOW)
    aid = outcome.alert_id
    assert sorted(deliveries(w)) == [
        (aid, "broken", "failed", 1), (aid, "healthy", "sent", 1), (aid, "in_app", "sent", 1),
    ]  # fmt: skip
    assert len(healthy.sent) == 1 and healthy.sent[0].ticker == "AAA"
    assert w.store.alerts[aid].delivery("broken").error == "simulated outage"


def test_failed_deliveries_are_retried_with_backoff_up_to_the_attempt_limit():
    w = World(conviction=80.0)
    w.seed("AAA")
    flaky = FakeChannel("flaky", fail=True)
    w.channels = [w.in_app, flaky]
    w.process.execute(w.new_signal_ids(), NOW)
    assert [d[2:] for d in deliveries(w, "flaky")] == [("failed", 1)]

    # backoff is [2, 10, 30, 120, 360] minutes: nothing is due a minute after the first failure
    assert w.deliver.execute(NOW + timedelta(minutes=1)) == 0
    assert flaky.sent == [] and [d[3] for d in deliveries(w, "flaky")] == [1]

    when = NOW
    for waited, attempts in ((2, 2), (10, 3), (30, 4), (120, 5), (360, 6)):
        when += timedelta(minutes=waited)
        w.deliver.execute(when)  # still failing
        assert [d[3] for d in deliveries(w, "flaky")] == [attempts]

    flaky.fail = False  # the channel recovers, but the attempt budget (6) is spent
    assert w.deliver.execute(when + timedelta(days=1)) == 0
    assert flaky.sent == []


def test_a_channel_that_recovers_within_the_budget_gets_its_alert():
    w = World(conviction=80.0)
    w.seed("BBB")
    other = FakeChannel("other", fail=True)
    w.channels = [other]
    w.process.execute(w.new_signal_ids(), NOW)
    other.fail = False
    assert w.deliver.execute(NOW + timedelta(minutes=3)) == 1
    assert deliveries(w, "other")[0][2:] == ("sent", 2)
    assert [m.ticker for m in other.sent] == ["BBB"]
    assert w.deliver.execute(NOW + timedelta(days=2)) == 0  # sent deliveries are never re-sent


def test_a_muted_channel_is_skipped_but_the_feed_is_not():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BBB")
    telegramish = FakeChannel("telegram")
    w.channels = [w.in_app, telegramish]
    ChannelService(w.uow_factory, w.clock).mute("telegram", NOW + timedelta(hours=1))
    (a,) = w.process.execute(w.new_signal_ids(["AAA"]), NOW)
    assert telegramish.sent == []
    assert sorted(d[1:3] for d in deliveries(w)) == [("in_app", "sent"), ("telegram", "skipped")]
    # after the mute expires the next alert is delivered normally
    w.process.execute(w.new_signal_ids(["BBB"]), NOW + timedelta(hours=2))
    assert [m.ticker for m in telegramish.sent] == ["BBB"]
    assert a.alert_id is not None


def test_enabling_a_channel_later_never_replays_earlier_alerts():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BBB")
    old = w.process.execute(w.new_signal_ids(["AAA"]), NOW)[0]

    telegramish = FakeChannel("telegram")  # switched on only now
    w.channels = [w.in_app, telegramish]
    assert w.deliver.execute(NOW) == 0
    new = w.process.execute(w.new_signal_ids(["BBB"]), NOW)[0]
    assert [m.ticker for m in telegramish.sent] == ["BBB"]  # only the new alert
    assert w.store.alerts[old.alert_id].delivery("telegram") is None
    assert w.store.alerts[new.alert_id].delivery("telegram").status is DeliveryStatus.SENT


def test_a_stricter_policy_is_honoured():
    w = World(conviction=80.0, policy=AlertPolicy(max_delivery_attempts=1))
    w.seed("AAA")
    flaky = FakeChannel("flaky", fail=True)
    w.channels = [w.in_app, flaky]
    w.process.execute(w.new_signal_ids(), NOW)
    flaky.fail = False
    assert w.deliver.execute(NOW + timedelta(days=1)) == 0  # one attempt only, already spent


# --- the feed -------------------------------------------------------------------------------------


def test_feed_pages_newest_first_and_tracks_read_state():
    w = World(conviction=80.0)
    for t in ("AAA", "BBB", "CCC"):
        w.seed(t)
    w.process.execute(w.new_signal_ids(), NOW)
    feed = AlertFeed(w.uow_factory, w.clock)
    page = feed.page(limit=2)
    assert [a.id for a in page] == [3, 2]
    assert [a.id for a in feed.page(limit=2, before_id=2)] == [1]  # the cursor
    assert (
        all(a.read_at is None for a in page) and page[0].delivery("in_app").status.value == "sent"
    )

    feed.mark_read(3)
    assert [a.id for a in feed.page(unread_only=True)] == [2, 1]
    assert feed.page(limit=1)[0].read_at == NOW
    with pytest.raises(NotFound):
        feed.mark_read(99999)


def test_the_feed_hides_the_chat_hint_but_the_stored_body_keeps_it():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.process.execute(w.new_signal_ids(), NOW)
    (alert,) = AlertFeed(w.uow_factory, w.clock).page()
    assert "Use /why AAA" in alert.body  # what Telegram sends
    assert "Use /why" not in alert.visible_body and "Scores:" in alert.visible_body
