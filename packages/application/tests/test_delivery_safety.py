"""Crash windows, the claim, muting at due time, and finishing interrupted work."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from scenario import AS_OF, NOW, World

from fin_application.alerts import ProcessSignals
from fin_application.errors import ChannelError, ConcurrencyConflict
from fin_domain.signals import ChannelState, DeliveryStatus
from fin_testkit import FakeChannel


def only_delivery(w, channel):
    (alert,) = w.store.alerts.values()
    return alert.delivery(channel)


class Crash:
    def execute(self, *args, **kwargs):
        raise OSError("crash")


def test_an_alert_committed_before_a_crash_is_delivered_by_the_next_pass():
    w = World(conviction=80.0)
    w.seed("AAA")
    chan = FakeChannel("chan")
    w.channels = [w.in_app, chan]
    crashing = ProcessSignals(w.uow_factory, w.clock, w.policy, lambda: w.channels, Crash())
    (outcome,) = crashing.execute(w.new_signal_ids(), NOW)
    assert outcome.alert_id is not None and outcome.error is None  # the decision survived
    d = only_delivery(w, "chan")
    assert (d.status, d.attempts) == (DeliveryStatus.PENDING, 0)
    assert w.deliver.execute(NOW) == 1
    assert [m.alert_id for m in chan.sent] == [outcome.alert_id]
    d = only_delivery(w, "chan")
    assert (d.status, d.attempts, d.error) == (DeliveryStatus.SENT, 1, None)


def test_a_pending_row_left_by_a_dead_process_is_retried_after_the_timeout():
    w = World(conviction=80.0)
    w.seed("AAA")
    chan = FakeChannel("chan")
    w.channels = [chan]
    w.process.execute(w.new_signal_ids(), NOW)
    with w.uow_factory() as uow:  # claimed (attempts=1, pending) but the process died
        (alert,) = uow.alert_feed.page()
        d = alert.delivery("chan")
        d.status, d.attempts, d.last_attempt_at = DeliveryStatus.PENDING, 1, NOW
        uow.alerts.save_delivery(d)
    chan.sent.clear()
    assert w.deliver.execute(NOW + timedelta(minutes=1)) == 0
    assert w.deliver.execute(NOW + timedelta(minutes=11)) == 1
    d = only_delivery(w, "chan")
    assert (d.status, d.attempts, d.error) == (DeliveryStatus.SENT, 2, None)


def test_a_claim_succeeds_for_one_caller_only():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.channels = [w.in_app, FakeChannel("chan")]
    ProcessSignals(w.uow_factory, w.clock, w.policy, lambda: w.channels, Crash()).execute(
        w.new_signal_ids(), NOW
    )
    with w.uow_factory() as uow:
        ((delivery, _),) = uow.alerts.open_deliveries("chan", 6)
        stale = delivery
    with w.uow_factory() as uow:
        mine = uow.alerts.open_deliveries("chan", 6)[0][0]
        mine.claim(NOW)
        uow.alerts.save_delivery(mine, expected_attempts=0)  # wins
    with w.uow_factory() as uow:
        stale.claim(NOW)
        with pytest.raises(ConcurrencyConflict):
            uow.alerts.save_delivery(stale, expected_attempts=0)  # same expectation, now stale
    assert only_delivery(w, "chan").attempts == 1


def test_nothing_is_held_open_while_a_channel_sends():
    w = World(conviction=80.0)
    w.seed("AAA")
    seen = {}

    def peek(message):
        # committed state, as another process would see it while we are inside send()
        seen["alerts"] = len(w.store.alerts)
        seen["delivery"] = w.store.alerts[message.alert_id].delivery("chan").status.value

    chan = FakeChannel("chan")
    chan.on_send = peek
    w.channels = [chan]
    w.process.execute(w.new_signal_ids(), NOW)
    assert seen == {"alerts": 1, "delivery": "pending"}  # claimed and committed, then sent


def test_an_unexpected_channel_error_is_recorded_by_type_only():
    w = World(conviction=80.0)
    w.seed("AAA")
    chan = FakeChannel("chan", crash=RuntimeError("secret-token-123 in the message"))
    w.channels = [chan]
    w.process.execute(w.new_signal_ids(), NOW)
    d = only_delivery(w, "chan")
    assert (d.status, d.attempts, d.error) == (DeliveryStatus.FAILED, 1, "unexpected RuntimeError")


def test_a_channel_muted_when_a_retry_is_due_skips_the_owed_delivery():
    w = World(conviction=80.0)
    w.seed("AAA")
    chan = FakeChannel("telegram", fail=True)
    w.channels = [chan]
    w.process.execute(w.new_signal_ids(), NOW)
    assert only_delivery(w, "telegram").status is DeliveryStatus.FAILED
    with w.uow_factory() as uow:
        uow.channels.save(ChannelState("telegram", NOW + timedelta(hours=5)))
    chan.fail = False
    assert w.deliver.execute(NOW + timedelta(minutes=5)) == 0
    assert chan.sent == [] and only_delivery(w, "telegram").status is DeliveryStatus.SKIPPED


def test_a_channel_error_with_a_credential_free_message_is_kept():
    w = World(conviction=80.0)
    w.seed("AAA")
    chan = FakeChannel("chan", fail=True)
    w.channels = [chan]
    w.process.execute(w.new_signal_ids(), NOW)
    assert only_delivery(w, "chan").error == "simulated outage"
    assert isinstance(ChannelError("x"), RuntimeError)


# --- interrupted signal processing ----------------------------------------------------------------


def test_one_failing_signal_loses_no_other_and_is_finished_by_the_sweep():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BBB")
    ids = w.new_signal_ids()
    process = w.process
    real = process.create_alert
    first = ids[0]

    def flaky(uow, signal_id, *a, **k):
        if signal_id == first:
            raise RuntimeError("boom")
        return real(uow, signal_id, *a, **k)

    process.create_alert = flaky
    out = process.execute(ids, NOW)
    assert out[0].error and out[0].alert_id is None and out[1].alert_id is not None
    with w.uow_factory() as uow:
        assert uow.signals.pending_ids() == [first]  # still owed

    with w.uow_factory() as uow:
        owed = uow.signals.pending_ids()
    assert w.process.execute(owed, NOW)[0].alert_id is not None
    with w.uow_factory() as uow:
        assert uow.signals.pending_ids() == [] and len(uow.alert_feed.page()) == 2


def test_a_deliberately_suppressed_signal_is_not_swept_up_again():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BBB")
    w.process.execute(w.new_signal_ids(), NOW)
    w.engine.conviction = 40.0
    sells = w.new_signal_ids(as_of=date(2025, 3, 12))  # not watched => suppressed
    out = w.process.execute(sells, NOW + timedelta(hours=30))
    assert all(o.alert_id is None and o.suppressed for o in out)
    with w.uow_factory() as uow:
        assert uow.signals.pending_ids() == []
    assert all(
        "watchlist" in s.suppressed_reason
        for s in w.store.signals.values()
        if s.signal.kind == "sell"
    )
    summary = w.run_scoring.execute(as_of=date(2025, 3, 12))
    assert summary.alerts == 0 and len(w.store.alerts) == 2  # only the two emerging alerts


def test_a_run_reports_partial_for_a_failing_signal_and_finishes_it_next_time():
    w = World(conviction=80.0)
    w.seed("AAA")
    process = w.process
    process.create_alert = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    broken = w.run_scoring
    broken._process = process
    first = broken.execute(as_of=AS_OF)
    assert first.status == "partial" and any(k.startswith("signal ") for k in first.failed)
    second = w.run_scoring.execute(as_of=AS_OF)
    assert second.status == "ok" and second.alerts == 1 and second.signals == 0
    with w.uow_factory() as uow:
        assert uow.signals.pending_ids() == []
