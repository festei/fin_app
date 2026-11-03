"""The signal and alert rules, with no database and no channel."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from fin_domain.scoring import Assessment, Criterion, ScoreResult, SecurityMetrics
from fin_domain.shared import InvariantViolation, Ticker
from fin_domain.signals import (
    COMMAND_HINT,
    IN_APP,
    Alert,
    AlertPolicy,
    ChannelState,
    Delivery,
    DeliveryStatus,
    LastAlert,
    MetricsSummary,
    ScoreDetails,
    ScoreEntry,
    Signal,
    StoredSignal,
    armed_after,
    buy_armed,
    compose_alert,
    is_actionable,
    sell_armed,
)

NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)
POLICY = AlertPolicy()


# --- policy ------------------------------------------------------------------------------------


def decide(kind="buy", conviction=80.0, watched=False, last=None, now=NOW, policy=POLICY):
    return policy.decide(
        kind=kind, conviction=conviction, watched=watched, last_alert=last, now=now
    )


def last_alert(hours_ago, conviction=80.0, naive=False):
    created = NOW - timedelta(hours=hours_ago)
    return LastAlert(created.replace(tzinfo=None) if naive else created, conviction)


def test_default_policy_values():
    assert (POLICY.cooldown_hours, POLICY.material_change) == (24, 10)
    assert POLICY.sell_only_for_watchlist is True and POLICY.max_delivery_attempts == 6
    assert POLICY.retry_backoff_minutes == (2, 10, 30, 120, 360)


def test_sells_alert_only_for_watched_stocks_buys_for_everyone():
    assert decide("sell", watched=False)[0] is False
    assert "watchlist" in decide("sell", watched=False)[1]
    assert decide("sell", watched=True)[0] is True
    assert decide("buy", watched=False)[0] is True and decide("emerging")[0] is True


def test_the_sell_rule_can_be_switched_off():
    assert decide("sell", watched=False, policy=AlertPolicy(sell_only_for_watchlist=False))[0]


def test_a_cooldown_suppresses_a_repeat_unless_conviction_moved_materially():
    ok, why = decide(conviction=82.0, last=last_alert(1, 80.0))
    assert ok is False and "cooldown" in why and "2.0" in why
    ok, why = decide(conviction=95.0, last=last_alert(1, 80.0))  # moved 15 >= 10
    assert ok is True and "moved 15.0" in why
    assert decide(conviction=82.0, last=last_alert(25, 80.0))[0] is True  # cooldown over


def test_the_policy_refuses_a_naive_time_instead_of_guessing_its_zone():
    # SQLite hands datetimes back naive; the persistence adapter makes them aware (contract test)
    with pytest.raises(InvariantViolation):
        decide(conviction=82.0, last=last_alert(1, 80.0, naive=True))


def test_severity_by_kind_and_watchlist():
    assert POLICY.severity_for("buy", False) == "high"
    assert POLICY.severity_for("emerging", False) == "normal"
    assert POLICY.severity_for("emerging", True) == "high"  # followed stocks are always high


@pytest.mark.parametrize(
    ("status", "attempts", "ago_minutes", "due"),
    [
        ("pending", 0, None, True),  # created with the alert, never tried
        ("failed", 1, 1, False),  # first retry waits 2 minutes
        ("failed", 1, 3, True),
        ("failed", 3, 29, False),  # the third wait is 30 minutes
        ("failed", 3, 31, True),
        ("failed", 5, 361, True),
        ("failed", 6, 10_000, False),  # out of attempts
        ("pending", 1, 5, False),  # an attempt that may still be running
        ("pending", 1, 11, True),  # ...until its process is presumed dead (10 minutes)
    ],
)
def test_when_a_delivery_is_due(status, attempts, ago_minutes, due):
    last = None if ago_minutes is None else NOW - timedelta(minutes=ago_minutes)
    assert POLICY.is_due(status=status, attempts=attempts, last_attempt_at=last, now=NOW) is due


def test_the_last_backoff_step_repeats():
    policy = AlertPolicy(max_delivery_attempts=20, retry_backoff_minutes=(1, 5))
    last = NOW - timedelta(minutes=6)
    assert policy.is_due(status="failed", attempts=9, last_attempt_at=last, now=NOW)
    assert not policy.is_due(
        status="failed", attempts=9, last_attempt_at=NOW - timedelta(minutes=4), now=NOW
    )


# --- delivery state machine --------------------------------------------------------------------


def test_claiming_takes_one_attempt_and_makes_the_delivery_pending():
    d = Delivery("telegram", DeliveryStatus.FAILED, attempts=2, error="boom")
    d.claim(NOW)
    assert (d.attempts, d.status, d.last_attempt_at) == (3, DeliveryStatus.PENDING, NOW)


@pytest.mark.parametrize("status", [DeliveryStatus.SENT, DeliveryStatus.SKIPPED])
def test_a_finished_delivery_cannot_be_claimed(status):
    with pytest.raises(InvariantViolation):
        Delivery("telegram", status).claim(NOW)


def test_sent_failed_and_skipped_record_their_outcome():
    d = Delivery("telegram")
    d.claim(NOW)
    d.mark_failed("x" * 900)
    assert d.status is DeliveryStatus.FAILED and len(d.error) == 500 and d.delivered_at is None
    d.claim(NOW + timedelta(minutes=3))
    d.mark_sent(NOW + timedelta(minutes=3))
    assert (d.status, d.error, d.attempts) == (DeliveryStatus.SENT, None, 2)
    assert d.delivered_at == NOW + timedelta(minutes=3)
    other = Delivery("telegram")
    other.skip("channel muted")
    assert (other.status, other.error, other.is_open) == (
        DeliveryStatus.SKIPPED,
        "channel muted",
        False,
    )


def alert(**kw):
    base = dict(
        ticker=Ticker("AAA"),
        kind="buy",
        severity="high",
        conviction=80.0,
        title="t",
        body="Scores: x\nUse /why AAA for the full breakdown.",
        created_at=NOW,
        signal_id=1,
    )
    return Alert(**{**base, **kw})


def test_a_new_alert_gets_one_delivery_per_registered_channel():
    a = alert()
    a.open_deliveries_for([IN_APP, "telegram", "email"], muted={"email"}, now=NOW)
    by = {d.channel: d for d in a.deliveries}
    assert (by[IN_APP].status, by[IN_APP].attempts) == (
        DeliveryStatus.SENT,
        1,
    )  # the row is the feed
    assert by["telegram"].status is DeliveryStatus.PENDING and by["telegram"].attempts == 0
    assert (by["email"].status, by["email"].error) == (DeliveryStatus.SKIPPED, "channel muted")
    assert a.delivery("nope") is None


def test_a_channel_registered_later_has_no_delivery_for_earlier_alerts():
    a = alert()
    a.open_deliveries_for([IN_APP], muted=set(), now=NOW)
    assert a.delivery("telegram") is None


def test_the_feed_hides_the_chat_hint_but_the_stored_body_keeps_it():
    a = alert()
    assert COMMAND_HINT in a.body and COMMAND_HINT not in a.visible_body
    assert a.visible_body == "Scores: x"


def test_reading_an_alert_keeps_the_first_time():
    a = alert()
    a.mark_read(NOW)
    a.mark_read(NOW + timedelta(hours=1))
    assert a.read_at == NOW


def test_an_alert_needs_an_aware_creation_time():
    with pytest.raises(InvariantViolation):
        alert(created_at=datetime(2025, 3, 10))  # noqa: DTZ001


# --- channel state -----------------------------------------------------------------------------


def test_a_channel_is_muted_until_the_deadline_passes():
    state = ChannelState("telegram")
    assert not state.is_muted(NOW)
    state.mute_until(NOW + timedelta(hours=2))
    assert state.is_muted(NOW) and not state.is_muted(NOW + timedelta(hours=2))
    state.unmute()
    assert not state.is_muted(NOW)


def test_mute_times_are_normalised_to_utc():
    state = ChannelState("telegram")
    state.mute_until(datetime(2025, 3, 10, 14, tzinfo=timezone(timedelta(hours=2))))
    assert state.muted_until == datetime(2025, 3, 10, 12, tzinfo=UTC)
    with pytest.raises(InvariantViolation):
        state.mute_until(datetime(2025, 3, 10))  # noqa: DTZ001


# --- the message -------------------------------------------------------------------------------


def result(name, score, *passed, evaluated=True):
    return ScoreResult(name, score, [Criterion(c, True) for c in passed], evaluated=evaluated)


DETAILS = ScoreDetails(
    philosophies={
        "graham": result("graham", 57),
        "lynch": result("lynch", 100, "peg", "growth", "debt", "extra"),
        "buffett": result("buffett", 0, evaluated=False),
    },
    guardrail_reasons=("Altman Z'' 0.4 is in the distress zone",),
    guardrail_notes=("buyback-driven",),
    warnings=("share count is old",),
    metrics=MetricsSummary(price=25.0, price_date=date(2025, 3, 7), pe=10.0, pb=2.5),
)


def signal(kind="emerging", conviction=78.6):
    return Signal("AAA", kind, conviction, NOW, "why")


def test_the_message_names_the_stock_kind_scores_and_price():
    title, body = compose_alert(Ticker("AAA"), signal(), DETAILS)
    assert title == "AAA: EMERGING (conviction 79)"
    assert "Scores: graham 57, lynch 100" in body and "buffett" not in body.split("Scores:")[1][:40]
    assert "Agree: lynch" in body and "Passing: peg; growth; debt" in body
    assert "Price 25.00 (2025-03-07), P/E 10.0, P/B 2.5" in body
    assert "Note: buyback-driven" in body and "Data: share count is old" in body
    assert body.endswith("Use /why AAA for the full breakdown.")


def test_a_sell_explains_the_guardrail_cap_instead_of_agreeing():
    _, body = compose_alert(Ticker("AAA"), signal("sell", 40), DETAILS)
    assert "Capped by guardrails: Altman Z'' 0.4 is in the distress zone" in body
    assert "Agree:" not in body


def test_a_message_without_stored_details_is_still_complete():
    title, body = compose_alert(Ticker("AAA"), signal("buy", 91), None)
    assert title == "AAA: BUY (conviction 91)" and body == "Use /why AAA for the full breakdown."


# --- stored scores and the rules that read them ------------------------------------------------


def test_only_a_score_with_a_conviction_on_unblocked_data_is_actionable():
    assert is_actionable(60.0, False)
    assert not is_actionable(60.0, True)
    assert not is_actionable(None, False)


def assessment(conviction, blockers=()):
    m = SecurityMetrics(
        ticker="AAA", as_of=NOW, price=25.0, price_date=date(2025, 3, 7), pe=10.0,
        warnings=["w"], signal_blockers=list(blockers),
    )  # fmt: skip
    return Assessment(m, conviction, conviction is None, "why", {"graham": result("graham", 57)})


def test_a_scoring_run_is_recorded_with_its_details():
    entry = ScoreEntry.record(assessment(78.6), "final text")
    assert (str(entry.ticker), entry.conviction, entry.explanation) == ("AAA", 78.6, "final text")
    assert entry.actionable and not entry.signal_blocked and not entry.all_abstained
    assert entry.details.metrics.pe == 10.0 and entry.details.warnings == ("w",)
    assert list(entry.details.philosophies) == ["graham"]


def test_a_run_on_poor_data_is_recorded_but_blocked():
    entry = ScoreEntry.record(assessment(78.6, ["price is stale"]), "x")
    assert entry.signal_blocked and not entry.actionable
    assert entry.details.signal_blockers == ("price is stale",)


def test_a_run_where_nothing_was_evaluated_has_no_conviction():
    entry = ScoreEntry.record(assessment(None), "x")
    assert entry.conviction is None and entry.all_abstained and not entry.actionable


def test_with_no_earlier_signal_both_sides_are_armed():
    assert buy_armed(None) and sell_armed(None)


def test_after_a_buy_the_next_buy_needs_conviction_to_have_fallen_below_65():
    assert not buy_armed([72.0, 70.0, 66.0])  # hovering above the re-arm line
    assert buy_armed([72.0, 64.9])
    assert not buy_armed([])  # nothing since the signal


def test_after_a_sell_the_next_sell_needs_conviction_back_at_55():
    assert not sell_armed([45.0, 54.9])
    assert sell_armed([45.0, 55.0])


def test_armed_after_takes_any_predicate():
    assert armed_after([1, 2, 3], lambda c: c > 2)
    assert not armed_after([1, 2], lambda c: c > 2)


def test_a_signal_starts_pending_and_is_marked_processed_once_decided():
    stored = StoredSignal(Ticker("AAA"), signal(), score_id=7)
    assert stored.is_pending
    stored.mark_processed(NOW)
    assert not stored.is_pending and stored.suppressed_reason is None


def test_a_suppression_reason_is_kept_but_cut_to_the_stored_length():
    stored = StoredSignal(Ticker("AAA"), signal(), score_id=7)
    stored.mark_processed(NOW, "r" * 500)
    assert stored.suppressed_reason == "r" * 200
