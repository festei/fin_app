"""One scheduled scoring run: score, alert, deliver, and be recorded."""

from __future__ import annotations

import pytest
from scenario import AS_OF, World

from fin_application.errors import ChannelConfigError
from fin_domain.shared import RunStatus


def runs(w: World):
    return [r for _, r in sorted(w.store.job_runs.items()) if r.source == "scoring"]


def test_a_run_scores_alerts_and_is_tracked():
    w = World()
    for t in ("AAA", "BBB", "CCC"):
        w.seed(t)
    w.engine.per_ticker = {"AAA": 78.5, "BBB": 92.8, "CCC": 38.1}  # CCC: nothing to say
    summary = w.run_scoring.execute(as_of=AS_OF)
    assert (summary.status, summary.rows, summary.signals, summary.alerts) == ("ok", 3, 2, 2)
    (run,) = runs(w)
    assert (run.status, run.rows, run.error) == (RunStatus.OK, 3, None) and run.finished_at
    assert sorted(a.kind for a in w.store.alerts.values()) == ["emerging", "emerging"]


def test_running_again_creates_nothing_new():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.run_scoring.execute(as_of=AS_OF)
    again = w.run_scoring.execute(as_of=AS_OF)
    assert (again.status, again.rows, again.signals, again.alerts) == ("ok", 1, 0, 0)
    assert len(w.store.alerts) == 1


def test_the_run_defaults_to_today_by_the_clock():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.run_scoring.execute()
    (entry,) = w.store.scores.values()
    assert entry.as_of.date() == w.clock.now().date()


def test_one_failing_security_makes_the_run_partial():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.seed("BAD")
    w.engine.raise_for = {"BAD"}
    summary = w.run_scoring.execute(as_of=AS_OF)
    assert summary.status == "partial" and list(summary.failed) == ["BAD"] and summary.alerts == 1
    assert runs(w)[0].status is RunStatus.PARTIAL and "BAD" in runs(w)[0].error


def test_every_security_failing_makes_the_run_failed():
    w = World(conviction=80.0)
    w.seed("AAA")
    w.engine.raise_for = {"AAA"}
    assert w.run_scoring.execute(as_of=AS_OF).status == "failed"


def test_an_empty_universe_is_an_ok_no_op():
    summary = World().run_scoring.execute(as_of=AS_OF)
    assert (summary.status, summary.rows, summary.alerts) == ("ok", 0, 0)


def test_invalid_channel_configuration_fails_the_run_loudly_before_anything_happens():
    w = World(conviction=80.0)
    w.seed("AAA")

    def broken():
        raise ChannelConfigError("telegram_enabled is on but TELEGRAM_BOT_TOKEN is not set")

    run = w.run_scoring
    run._channels = broken
    with pytest.raises(ChannelConfigError):
        run.execute(as_of=AS_OF)
    (record,) = runs(w)
    assert record.status is RunStatus.FAILED and "TELEGRAM_BOT_TOKEN" in record.error
    assert w.store.scores == {} and w.store.alerts == {}  # nothing half-done


def test_the_feed_gets_the_alert_even_with_only_the_in_app_channel():
    w = World(conviction=80.0)
    w.seed("AAA")
    summary = w.run_scoring.execute(as_of=AS_OF)
    assert summary.status == "ok" and summary.alerts == 1
    (alert,) = w.store.alerts.values()
    assert {d.channel: d.status.value for d in alert.deliveries} == {"in_app": "sent"}
