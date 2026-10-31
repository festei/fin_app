"""Alert policy, message text, delivery tracking, retries, muting and the no-replay rule."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import bar
from sqlalchemy import func, select

from fin_core.alerts.base import AlertMessage, ChannelError
from fin_core.alerts.channels import InAppChannel
from fin_core.alerts.pipeline import (
    build_message_text,
    deliver,
    feed_item,
    process_signals,
    retry_failed,
)
from fin_core.alerts.policy import AlertPolicy
from fin_core.db import repo, signals_repo
from fin_core.db.models import AlertDelivery, AlertRecord, ScoreRecord, SignalRecord
from fin_core.db.session import session_scope
from fin_core.signals import score_universe

NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)
AS_OF = date(2025, 3, 10)
POLICY = AlertPolicy.load()
IN_APP = InAppChannel()


class FakeChannel:
    """Records what it was asked to send; can be told to fail."""

    def __init__(self, name="fake", fail=False):
        self.name, self.fail, self.sent = name, fail, []

    def send(self, message: AlertMessage) -> None:
        if self.fail:
            raise ChannelError("simulated outage")
        self.sent.append(message)


def new_signal_ids(engine, tickers=None, as_of=AS_OF):
    return [sid for sid, _, _ in score_universe(engine, as_of, tickers=tickers).signals]


def alerts(engine):
    with session_scope(engine) as s:
        return [(a.id, a.kind, a.severity) for a in signals_repo.list_alerts(s, limit=100)]


def deliveries(engine):
    with session_scope(engine) as s:
        rows = s.scalars(select(AlertDelivery).order_by(AlertDelivery.id))
        return [(d.alert_id, d.channel, d.status, d.attempts) for d in rows]


def manual_signal(engine, ticker, kind, conviction):
    """A signal row tied to the security's latest score, for policy tests that need control."""
    with session_scope(engine) as s:
        sid = repo.get_security(s, ticker).id
        score = signals_repo.latest_score(s, sid)
        return signals_repo.insert_signal(
            s, security_id=sid, score_id=score.id, kind=kind, conviction=conviction,
            as_of=NOW, reason="",
        ).id  # fmt: skip


# --- policy -----------------------------------------------------------------------------------


def decide(kind="buy", conviction=80.0, watched=False, last=None, now=NOW, policy=POLICY):
    return policy.decide(
        kind=kind, conviction=conviction, watched=watched, last_alert=last, now=now
    )


def last_alert(hours_ago, conviction=80.0, naive=False):
    created = NOW - timedelta(hours=hours_ago)
    return AlertRecord(created_at=created.replace(tzinfo=None) if naive else created,
                       conviction=conviction)  # fmt: skip


def test_default_policy_values():
    assert (POLICY.cooldown_hours, POLICY.material_change) == (24, 10)
    assert POLICY.sell_only_for_watchlist is True and POLICY.max_delivery_attempts == 3


def test_sells_alert_only_for_watched_stocks_buys_for_everyone():
    assert decide("sell", watched=False)[0] is False
    assert "watchlist" in decide("sell", watched=False)[1]
    assert decide("sell", watched=True)[0] is True
    assert decide("buy", watched=False)[0] is True and decide("emerging")[0] is True


def test_sell_rule_can_be_switched_off():
    loose = AlertPolicy(sell_only_for_watchlist=False)
    assert decide("sell", watched=False, policy=loose)[0] is True


def test_cooldown_suppresses_a_repeat_unless_conviction_moved_materially():
    ok, why = decide(conviction=82.0, last=last_alert(1, 80.0))
    assert ok is False and "cooldown" in why and "2.0" in why
    ok, why = decide(conviction=95.0, last=last_alert(1, 80.0))  # moved 15 >= 10
    assert ok is True and "moved 15.0" in why
    assert decide(conviction=82.0, last=last_alert(25, 80.0))[0] is True  # cooldown over
    assert decide(conviction=82.0, last=last_alert(1, 80.0, naive=True))[0] is False  # SQLite


def test_severity_by_kind_and_watchlist():
    assert POLICY.severity_for("buy", False) == "high"
    assert POLICY.severity_for("emerging", False) == "normal"
    assert POLICY.severity_for("emerging", True) == "high"  # followed stocks are always high


def test_unknown_policy_fields_are_rejected(tmp_path):
    bad = tmp_path / "alerts.yml"
    bad.write_text("alerts:\n  cooldown_hours: 1\n  colour: red\n")
    with pytest.raises(ValueError, match="unknown alert policy fields"):
        AlertPolicy.load(bad)


# --- message text and the feed --------------------------------------------------------------


def test_message_text_names_the_stock_kind_scores_and_price(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    (signal_id,) = new_signal_ids(raw_engine)
    with session_scope(raw_engine) as s:
        sig = s.get(SignalRecord, signal_id)
        sec = repo.get_security(s, "AAA")
        title, body = build_message_text(sec, sig, s.get(ScoreRecord, sig.score_id))
    assert title.startswith("AAA: EMERGING (conviction ") and len(title) <= 200
    assert "Scores: graham 57, lynch 100" in body
    assert "Agree: lynch" in body and "graham" not in body.split("Agree:")[1].split("\n")[0]
    assert "Price 25.00 (2025-03-03), P/E 10.0, P/B 2.5" in body
    assert "Use /why AAA" in body


# --- creating and delivering alerts -----------------------------------------------------------


def test_a_signal_becomes_one_alert_with_an_in_app_delivery(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    ids = new_signal_ids(raw_engine)
    (outcome,) = process_signals(raw_engine, ids, POLICY, [IN_APP], NOW)
    assert outcome.alert_id is not None and outcome.suppressed is None
    assert alerts(raw_engine) == [(outcome.alert_id, "emerging", "normal")]
    assert deliveries(raw_engine) == [(outcome.alert_id, "in_app", "sent", 1)]

    again = process_signals(raw_engine, ids, POLICY, [IN_APP], NOW)  # same signal, e.g. a re-run
    assert again[0].alert_id == outcome.alert_id
    assert len(alerts(raw_engine)) == 1 and len(deliveries(raw_engine)) == 1  # nothing duplicated


def test_sell_alerts_only_for_followed_stocks(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    seed_company(raw_engine, "BBB", price=25.0)
    process_signals(raw_engine, new_signal_ids(raw_engine), POLICY, [IN_APP], NOW)  # both emerging
    with session_scope(raw_engine) as s:
        signals_repo.watchlist_add(s, repo.get_security(s, "BBB").id)
        for t in ("AAA", "BBB"):
            repo.upsert_prices(s, repo.get_security(s, t).id, [bar(date(2025, 3, 11), 100.0)])

    sells = new_signal_ids(raw_engine, as_of=date(2025, 3, 12))
    assert len(sells) == 2  # both stocks fall below the sell line
    out = process_signals(raw_engine, sells, POLICY, [IN_APP], NOW + timedelta(hours=30))
    by_ticker = {o.ticker: o for o in out}
    assert by_ticker["AAA"].alert_id is None and "watchlist" in by_ticker["AAA"].suppressed
    assert by_ticker["BBB"].alert_id is not None
    sell_alerts = [a for a in alerts(raw_engine) if a[1] == "sell"]
    assert len(sell_alerts) == 1 and sell_alerts[0][2] == "high"  # followed => high severity


def test_cooldown_in_the_pipeline_and_material_change_realert(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    new_signal_ids(raw_engine)
    first = manual_signal(raw_engine, "AAA", "buy", 80.0)
    assert process_signals(raw_engine, [first], POLICY, [IN_APP], NOW)[0].alert_id
    quiet = manual_signal(raw_engine, "AAA", "buy", 83.0)
    out = process_signals(raw_engine, [quiet], POLICY, [IN_APP], NOW + timedelta(hours=2))
    assert out[0].alert_id is None and "cooldown" in out[0].suppressed
    jump = manual_signal(raw_engine, "AAA", "buy", 95.0)
    assert process_signals(raw_engine, [jump], POLICY, [IN_APP], NOW + timedelta(hours=3))[
        0
    ].alert_id
    later = manual_signal(raw_engine, "AAA", "buy", 96.0)
    assert process_signals(raw_engine, [later], POLICY, [IN_APP], NOW + timedelta(hours=30))[
        0
    ].alert_id
    with session_scope(raw_engine) as s:
        assert s.scalar(select(func.count()).select_from(AlertRecord)) == 3


def test_a_failing_channel_is_recorded_and_never_blocks_the_others(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    broken, healthy = FakeChannel("broken", fail=True), FakeChannel("healthy")
    (outcome,) = process_signals(
        raw_engine, new_signal_ids(raw_engine), POLICY, [IN_APP, broken, healthy], NOW
    )
    aid = outcome.alert_id
    assert sorted(deliveries(raw_engine)) == [
        (aid, "broken", "failed", 1), (aid, "healthy", "sent", 1), (aid, "in_app", "sent", 1),
    ]  # fmt: skip
    assert len(healthy.sent) == 1 and healthy.sent[0].ticker == "AAA"
    with session_scope(raw_engine) as s:
        error = signals_repo.get_delivery(s, aid, "broken").error
    assert error == "simulated outage"


def test_failed_deliveries_are_retried_a_bounded_number_of_times(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    flaky = FakeChannel("flaky", fail=True)
    process_signals(raw_engine, new_signal_ids(raw_engine), POLICY, [IN_APP, flaky], NOW)
    assert retry_failed(raw_engine, [IN_APP, flaky], POLICY, NOW) == 0  # attempt 2 fails
    assert retry_failed(raw_engine, [IN_APP, flaky], POLICY, NOW) == 0  # attempt 3 fails
    assert [d[3] for d in deliveries(raw_engine) if d[1] == "flaky"] == [3]
    flaky.fail = False
    assert retry_failed(raw_engine, [IN_APP, flaky], POLICY, NOW) == 0  # budget is spent: no resend
    assert flaky.sent == []

    # a channel that recovers within the budget does get its alert
    other = FakeChannel("other", fail=True)
    seed_company(raw_engine, "BBB", price=25.0)
    process_signals(raw_engine, new_signal_ids(raw_engine, ["BBB"]), POLICY, [other], NOW)
    other.fail = False
    assert retry_failed(raw_engine, [other], POLICY, NOW) == 1
    assert [d for d in deliveries(raw_engine) if d[1] == "other"][0][2:] == ("sent", 2)


def test_a_muted_channel_is_skipped_but_the_feed_is_not(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    seed_company(raw_engine, "BBB", price=25.0)
    telegramish = FakeChannel("telegram")
    with session_scope(raw_engine) as s:
        signals_repo.set_muted_until(s, "telegram", NOW + timedelta(hours=1))
    (a,) = process_signals(
        raw_engine, new_signal_ids(raw_engine, ["AAA"]), POLICY, [IN_APP, telegramish], NOW
    )
    assert telegramish.sent == []
    assert sorted(d[1:3] for d in deliveries(raw_engine)) == [
        ("in_app", "sent"),
        ("telegram", "skipped"),
    ]
    # after the mute expires the next alert is delivered normally
    process_signals(
        raw_engine, new_signal_ids(raw_engine, ["BBB"]), POLICY, [IN_APP, telegramish],
        NOW + timedelta(hours=2),
    )  # fmt: skip
    assert [m.ticker for m in telegramish.sent] == ["BBB"]
    assert a.alert_id is not None


def test_enabling_a_channel_later_never_replays_earlier_alerts(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    seed_company(raw_engine, "BBB", price=25.0)
    old = process_signals(raw_engine, new_signal_ids(raw_engine, ["AAA"]), POLICY, [IN_APP], NOW)[0]

    telegramish = FakeChannel("telegram")  # switched on only now
    assert retry_failed(raw_engine, [IN_APP, telegramish], POLICY, NOW) == 0
    new = process_signals(
        raw_engine, new_signal_ids(raw_engine, ["BBB"]), POLICY, [IN_APP, telegramish], NOW
    )[0]
    assert [m.ticker for m in telegramish.sent] == ["BBB"]  # only the new alert
    with session_scope(raw_engine) as s:
        assert signals_repo.get_delivery(s, old.alert_id, "telegram") is None
        assert signals_repo.get_delivery(s, new.alert_id, "telegram").status == "sent"


def test_feed_items_and_read_state(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    (outcome,) = process_signals(raw_engine, new_signal_ids(raw_engine), POLICY, [IN_APP], NOW)
    with session_scope(raw_engine) as s:
        alert = s.get(AlertRecord, outcome.alert_id)
        item = feed_item(alert, "AAA", signals_repo.deliveries_for(s, [alert.id]))
        assert item["read"] is False and item["deliveries"] == {"in_app": "sent"}
        assert len(signals_repo.list_alerts(s, unread_only=True)) == 1
        assert signals_repo.mark_alert_read(s, alert.id, NOW) is True
        assert signals_repo.mark_alert_read(s, 99999, NOW) is False
        assert signals_repo.list_alerts(s, unread_only=True) == []
        assert signals_repo.list_alerts(s, before_id=alert.id) == []  # cursor
    assert deliver  # imported for completeness of the public surface
