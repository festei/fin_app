"""The scheduled scoring run: score -> signals -> alerts -> deliveries, with run tracking."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from fin_core.alerts.base import ChannelConfigError
from fin_core.alerts.channels import InAppChannel
from fin_core.db import signals_repo
from fin_core.db.models import AlertRecord, IngestionRun
from fin_core.db.session import session_scope
from fin_core.scoring_run import run_scoring

AS_OF = date(2025, 3, 10)


def runs(engine):
    with session_scope(engine) as s:
        return list(s.scalars(select(IngestionRun).where(IngestionRun.source == "scoring")))


def test_a_run_scores_alerts_and_is_tracked(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)  # 78.5 -> emerging
    seed_company(raw_engine, "BBB", price=12.0)  # 92.8 -> emerging
    seed_company(raw_engine, "CCC", price=100.0)  # 38.1 -> nothing
    summary = run_scoring(raw_engine, channels=[InAppChannel()], as_of=AS_OF)
    assert (summary.status, summary.rows, summary.signals, summary.alerts) == ("ok", 3, 2, 2)
    (run,) = runs(raw_engine)
    assert (run.status, run.rows, run.error) == ("ok", 3, None) and run.finished_at is not None
    with session_scope(raw_engine) as s:
        assert sorted(a.kind for a in signals_repo.list_alerts(s)) == ["emerging", "emerging"]


def test_running_again_creates_nothing_new(raw_engine, seed_company):
    seed_company(raw_engine, "AAA", price=25.0)
    run_scoring(raw_engine, channels=[InAppChannel()], as_of=AS_OF)
    again = run_scoring(raw_engine, channels=[InAppChannel()], as_of=AS_OF)
    assert (again.status, again.rows, again.signals, again.alerts) == ("ok", 1, 0, 0)
    with session_scope(raw_engine) as s:
        assert len(signals_repo.list_alerts(s)) == 1


def test_one_failing_security_makes_the_run_partial(raw_engine, seed_company, monkeypatch):
    seed_company(raw_engine, "AAA", price=25.0)
    seed_company(raw_engine, "BAD", price=25.0)
    import fin_core.signals.engine as engine_mod

    real = engine_mod.score_and_record

    def flaky(session, ticker, as_of, **kw):
        if ticker == "BAD":
            raise RuntimeError("boom")
        return real(session, ticker, as_of, **kw)

    monkeypatch.setattr(engine_mod, "score_and_record", flaky)
    summary = run_scoring(raw_engine, channels=[InAppChannel()], as_of=AS_OF)
    assert summary.status == "partial" and list(summary.failed) == ["BAD"] and summary.alerts == 1
    assert runs(raw_engine)[0].status == "partial" and "BAD" in runs(raw_engine)[0].error


def test_every_security_failing_makes_the_run_failed(raw_engine, seed_company, monkeypatch):
    seed_company(raw_engine, "AAA", price=25.0)
    import fin_core.signals.engine as engine_mod

    def boom(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(engine_mod, "score_and_record", boom)
    assert run_scoring(raw_engine, channels=[InAppChannel()], as_of=AS_OF).status == "failed"


def test_an_empty_universe_is_an_ok_no_op(raw_engine):
    summary = run_scoring(raw_engine, channels=[InAppChannel()], as_of=AS_OF)
    assert (summary.status, summary.rows, summary.alerts) == ("ok", 0, 0)


def test_telegram_on_without_credentials_fails_the_run_loudly(
    raw_engine, seed_company, monkeypatch
):
    seed_company(raw_engine, "AAA", price=25.0)
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ChannelConfigError):
        run_scoring(raw_engine, as_of=AS_OF)  # channels=None -> built from the feature flags
    (run,) = runs(raw_engine)
    assert run.status == "failed" and "TELEGRAM_BOT_TOKEN" in run.error
    with session_scope(raw_engine) as s:
        assert s.scalars(select(AlertRecord)).first() is None  # nothing half-done


def test_default_channels_follow_the_flags_and_the_feed_gets_the_alert(
    raw_engine, seed_company, monkeypatch
):
    seed_company(raw_engine, "AAA", price=25.0)
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    summary = run_scoring(raw_engine, as_of=AS_OF)  # channels=None, flag off
    assert summary.status == "ok" and summary.alerts == 1
    with session_scope(raw_engine) as s:
        (alert,) = signals_repo.list_alerts(s)
        assert {d.channel: d.status for d in signals_repo.deliveries_for(s, [alert.id])} == {
            "in_app": "sent"
        }
    assert datetime.now(UTC).date() >= AS_OF
