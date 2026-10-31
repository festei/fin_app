"""Delivery robustness: crash windows, the atomic claim, muting at due time, resumption of
interrupted signal processing, hysteresis against flapping signals, and migration 0003."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text

from fin_core.alerts import pipeline
from fin_core.alerts.base import AlertMessage, ChannelError
from fin_core.alerts.channels import InAppChannel
from fin_core.alerts.pipeline import process_signals, send_due
from fin_core.alerts.policy import AlertPolicy
from fin_core.db import migrate, repo, signals_repo
from fin_core.db.models import AlertDelivery, SignalRecord
from fin_core.db.session import session_scope
from fin_core.graph.build import BUY_REARM_BELOW, SELL_REARM_ABOVE, decide_node
from fin_core.models import SecurityMetrics
from fin_core.scoring_run import run_scoring
from fin_core.signals import score_universe

NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)
AS_OF = date(2025, 3, 10)
POLICY = AlertPolicy.load()
IN_APP = InAppChannel()


class Chan:
    def __init__(self, name="chan", error=None, on_send=None):
        self.name, self.error, self.on_send, self.sent = name, error, on_send, []

    def send(self, message: AlertMessage) -> None:
        if self.on_send:
            self.on_send(message)
        if self.error:
            raise self.error
        self.sent.append(message.alert_id)


def signal_ids(engine, tickers=None, as_of=AS_OF):
    return [sid for sid, _, _ in score_universe(engine, as_of, tickers=tickers).signals]


def deliveries(engine, channel="chan"):
    with session_scope(engine) as s:
        rows = s.scalars(select(AlertDelivery).where(AlertDelivery.channel == channel))
        return [(d.status, d.attempts, d.error) for d in rows]


# --- crash windows ------------------------------------------------------------------------------


def test_an_alert_committed_before_a_crash_is_delivered_by_the_next_pass(
    raw_engine, seed_company, monkeypatch
):
    seed_company(raw_engine, "AAA", price=25.0)
    chan = Chan()
    with monkeypatch.context() as m:
        m.setattr(pipeline, "send_due", lambda *a, **k: (_ for _ in ()).throw(OSError("crash")))
        (outcome,) = process_signals(
            raw_engine, signal_ids(raw_engine), POLICY, [IN_APP, chan], NOW
        )
    assert outcome.alert_id is not None and outcome.error is None  # the decision survived
    assert deliveries(raw_engine) == [("pending", 0, None)]
    assert send_due(raw_engine, [IN_APP, chan], POLICY, NOW) == 1
    assert chan.sent == [outcome.alert_id] and deliveries(raw_engine) == [("sent", 1, None)]


def test_a_pending_row_left_by_a_dead_process_is_retried_after_the_timeout(
    raw_engine, seed_company
):
    seed_company(raw_engine, "AAA", price=25.0)
    chan = Chan()
    process_signals(raw_engine, signal_ids(raw_engine), POLICY, [chan], NOW)
    with session_scope(raw_engine) as s:  # claimed (attempts=1, pending) but the process died
        d = s.scalars(select(AlertDelivery).where(AlertDelivery.channel == "chan")).one()
        d.status, d.attempts, d.last_attempt_at = "pending", 1, NOW
    chan.sent.clear()
    assert send_due(raw_engine, [chan], POLICY, NOW + timedelta(minutes=1)) == 0
    assert send_due(raw_engine, [chan], POLICY, NOW + timedelta(minutes=11)) == 1
    assert deliveries(raw_engine) == [("sent", 2, None)]


def test_a_claim_succeeds_for_one_caller_only(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(pipeline, "send_due", lambda *a, **k: 0)
        process_signals(raw_engine, signal_ids(raw_engine), POLICY, [Chan()], NOW)
    with session_scope(raw_engine) as s:
        did = s.scalars(select(AlertDelivery).where(AlertDelivery.channel == "chan")).one().id
    with session_scope(raw_engine) as s:
        assert signals_repo.claim_delivery(s, did, 0, NOW) is True
    with session_scope(raw_engine) as s:
        assert signals_repo.claim_delivery(s, did, 0, NOW) is False  # same expectation, now stale


def test_nothing_is_held_open_while_a_channel_sends(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    seen = {}

    def peek(message):
        with session_scope(raw_engine) as s:  # a separate session sees committed state
            seen["alerts"] = len(signals_repo.list_alerts(s))
            seen["delivery"] = signals_repo.get_delivery(s, message.alert_id, "chan").status

    process_signals(raw_engine, signal_ids(raw_engine), POLICY, [Chan(on_send=peek)], NOW)
    assert seen == {"alerts": 1, "delivery": "pending"}  # claimed and committed, then sent


def test_an_unexpected_channel_error_is_recorded_by_type_only(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    chan = Chan(error=RuntimeError("secret-token-123 in the message"))
    process_signals(raw_engine, signal_ids(raw_engine), POLICY, [chan], NOW)
    assert deliveries(raw_engine) == [("failed", 1, "unexpected RuntimeError")]


def test_a_channel_muted_when_a_retry_is_due_skips_the_owed_delivery(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    chan = Chan("telegram", error=ChannelError("down"))
    process_signals(raw_engine, signal_ids(raw_engine), POLICY, [chan], NOW)
    assert deliveries(raw_engine, "telegram")[0][:2] == ("failed", 1)
    with session_scope(raw_engine) as s:
        signals_repo.set_muted_until(s, "telegram", NOW + timedelta(hours=5))
    chan.error = None
    assert send_due(raw_engine, [chan], POLICY, NOW + timedelta(minutes=5)) == 0
    assert chan.sent == [] and deliveries(raw_engine, "telegram")[0][0] == "skipped"


def test_concurrent_senders_deliver_exactly_once(raw_engine, seed_company):
    if raw_engine.dialect.name != "postgresql":
        pytest.skip("needs real concurrent transactions")
    seed_company(raw_engine, "AAA", price=25.0)
    chan = Chan(on_send=lambda m: threading.Event().wait(0.2))  # widen the race window
    with pytest.MonkeyPatch.context() as m:
        m.setattr(pipeline, "send_due", lambda *a, **k: 0)
        process_signals(raw_engine, signal_ids(raw_engine), POLICY, [chan], NOW)
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        send_due(raw_engine, [chan], POLICY, NOW)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(chan.sent) == 1 and deliveries(raw_engine) == [("sent", 1, None)]


# --- interrupted signal processing --------------------------------------------------------------


def test_one_failing_signal_loses_no_other_and_is_finished_by_the_sweep(
    raw_engine, seed_company, monkeypatch
):
    seed_company(raw_engine, "AAA", price=25.0)
    seed_company(raw_engine, "BBB", price=25.0)
    ids = signal_ids(raw_engine)
    real = pipeline.create_alert
    first = ids[0]

    def flaky(session, signal_id, *a, **k):
        if signal_id == first:
            raise RuntimeError("boom")
        return real(session, signal_id, *a, **k)

    monkeypatch.setattr(pipeline, "create_alert", flaky)
    out = process_signals(raw_engine, ids, POLICY, [IN_APP], NOW)
    assert out[0].error and out[0].alert_id is None and out[1].alert_id is not None
    with session_scope(raw_engine) as s:
        assert signals_repo.pending_signal_ids(s) == [first]  # still owed

    monkeypatch.setattr(pipeline, "create_alert", real)
    with session_scope(raw_engine) as s:
        owed = signals_repo.pending_signal_ids(s)
    assert process_signals(raw_engine, owed, POLICY, [IN_APP], NOW)[0].alert_id is not None
    with session_scope(raw_engine) as s:
        assert signals_repo.pending_signal_ids(s) == [] and len(signals_repo.list_alerts(s)) == 2


def test_a_deliberately_suppressed_signal_is_not_swept_up_again(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    seed_company(raw_engine, "BBB", price=25.0)
    process_signals(raw_engine, signal_ids(raw_engine), POLICY, [IN_APP], NOW)
    for t in ("AAA", "BBB"):
        with session_scope(raw_engine) as s:
            repo.upsert_prices(s, repo.get_security(s, t).id, [_bar(date(2025, 3, 11), 100.0)])
    sells = signal_ids(raw_engine, as_of=date(2025, 3, 12))  # not watched => suppressed
    out = process_signals(raw_engine, sells, POLICY, [IN_APP], NOW + timedelta(hours=30))
    assert all(o.alert_id is None and o.suppressed for o in out)
    with session_scope(raw_engine) as s:
        assert signals_repo.pending_signal_ids(s) == []
        rows = s.scalars(select(SignalRecord).where(SignalRecord.kind == "sell")).all()
        assert all("watchlist" in r.suppressed_reason for r in rows)
    summary = run_scoring(raw_engine, channels=[IN_APP], as_of=date(2025, 3, 12))
    assert summary.alerts == 0
    with session_scope(raw_engine) as s:
        assert len(signals_repo.list_alerts(s)) == 2  # only the two emerging alerts


def test_a_run_reports_partial_for_a_failing_signal_and_finishes_it_next_time(
    raw_engine, seed_company, monkeypatch
):
    seed_company(raw_engine, "AAA", price=25.0)
    real = pipeline.create_alert
    monkeypatch.setattr(
        pipeline, "create_alert", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    first = run_scoring(raw_engine, channels=[IN_APP], as_of=AS_OF)
    assert first.status == "partial" and any(k.startswith("signal ") for k in first.failed)
    monkeypatch.setattr(pipeline, "create_alert", real)
    second = run_scoring(raw_engine, channels=[IN_APP], as_of=AS_OF)
    assert second.status == "ok" and second.alerts == 1
    with session_scope(raw_engine) as s:
        assert len(signals_repo.list_alerts(s)) == 1


def _bar(day, close):
    from conftest import bar

    return bar(day, close)


# --- hysteresis ---------------------------------------------------------------------------------


def decide(now, prev, **flags):
    m = SecurityMetrics(ticker="X", as_of=datetime(2025, 3, 10, tzinfo=UTC))
    state = {"conviction": now, "previous_conviction": prev, "metrics": m, **flags}
    sig = decide_node(state)["signal"]
    return sig.kind if sig else None


def test_a_score_flapping_around_the_buy_line_signals_once():
    assert decide(70.1, 69.9) == "buy"
    assert decide(70.1, 69.9, buy_armed=False) is None  # came back over without re-arming
    assert decide(70.1, 69.9, buy_armed=True) == "buy"
    assert BUY_REARM_BELOW < 70


def test_a_score_flapping_around_the_sell_line_signals_once():
    assert decide(49.9, 50.1) == "sell"
    assert decide(49.9, 50.1, sell_armed=False) is None
    assert SELL_REARM_ABOVE > 50


def _score(s, sid, conviction, blocked=False):
    return signals_repo.insert_score(
        s, security_id=sid, as_of=NOW, conviction=conviction, all_abstained=False,
        signal_blocked=blocked, explanation="", details={},
    )  # fmt: skip


def test_armed_state_follows_the_stored_history(raw_engine):
    with session_scope(raw_engine) as s:
        repo.upsert_securities(s, [{"ticker": "AAA"}])
        sid = repo.get_security(s, "AAA").id

        def armed():
            return signals_repo.armed_state(
                s, sid, buy_rearm_below=BUY_REARM_BELOW, sell_rearm_above=SELL_REARM_ABOVE
            )

        assert armed() == (True, True)  # no history
        a = _score(s, sid, 72.0)
        signals_repo.insert_signal(
            s, security_id=sid, score_id=a.id, kind="buy", conviction=72.0, as_of=NOW, reason=""
        )
        assert armed() == (False, True)
        _score(s, sid, 68.0)  # dipped, but not below the re-arm level
        assert armed() == (False, True)
        _score(s, sid, 30.0, blocked=True)  # a blocked run never counts
        assert armed() == (False, True)
        _score(s, sid, BUY_REARM_BELOW - 1)
        assert armed() == (True, True)  # re-armed


# --- migration 0003 -----------------------------------------------------------------------------


def test_migration_0003_marks_existing_signals_processed(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrate.upgrade(url, "0002")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text("INSERT INTO securities (id, ticker, kind, active) VALUES (1, 'AAA', 'stock', 1)")
        )
        c.execute(
            text(
                "INSERT INTO scores (id, security_id, as_of, computed_at, conviction,"
                " all_abstained,"
                " signal_blocked, explanation, details) VALUES"
                " (1, 1, '2025-03-10', '2025-03-10 12:00:00', 80, 0, 0, 'x', '{}')"
            )
        )
        c.execute(
            text(
                "INSERT INTO signals (id, security_id, score_id, kind, conviction, as_of, reason,"
                " created_at) VALUES (1, 1, 1, 'buy', 80, '2025-03-10', '', '2025-03-10 12:00:00')"
            )
        )
    eng.dispose()
    migrate.upgrade(url)
    eng = create_engine(url)
    with eng.connect() as c:
        processed, reason = c.execute(
            text("SELECT processed_at, suppressed_reason FROM signals")
        ).one()
    eng.dispose()
    assert processed is not None and reason is None
