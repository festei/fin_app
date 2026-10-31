import pytest
from prefect.testing.utilities import prefect_test_harness

from fin_core.db import migrate
from fin_core.db.session import make_engine
from fin_core.ingest import RunSummary
from fin_flows import scoring, serve


@pytest.fixture
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'scoring.db'}"
    migrate.upgrade(url)
    eng = make_engine(url)
    monkeypatch.setattr(scoring, "make_engine", lambda: eng)
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    return eng


def test_flow_runs_the_real_scoring_run_on_an_empty_universe(engine):
    with prefect_test_harness():
        out = scoring.score_universe_flow()
    assert out["source"] == "scoring" and out["status"] == "ok" and out["rows"] == 0


def test_flow_passes_the_tickers_through(engine, monkeypatch):
    seen = {}

    def fake_run(eng, *, tickers=None, **kw):
        seen["tickers"] = tickers
        return RunSummary("scoring", status="ok", rows=2, signals=1, alerts=1)

    monkeypatch.setattr(scoring, "run_scoring", fake_run)
    with prefect_test_harness():
        out = scoring.score_universe_flow(["AAA"])
    assert seen["tickers"] == ["AAA"] and (out["signals"], out["alerts"]) == (1, 1)


def test_flow_fails_when_the_whole_run_failed(engine, monkeypatch):
    monkeypatch.setattr(
        scoring,
        "run_scoring",
        lambda eng, **kw: RunSummary("scoring", status="failed", failed={"AAA": "boom"}),
    )
    with prefect_test_harness(), pytest.raises(RuntimeError, match="scoring run failed"):
        scoring.score_universe_flow()


def test_flow_fails_loudly_on_an_invalid_channel_configuration(engine, monkeypatch):
    from fin_core.alerts.base import ChannelConfigError

    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with prefect_test_harness(), pytest.raises(ChannelConfigError):
        scoring.score_universe_flow()


def test_schedules_cover_every_flow_and_scoring_runs_after_the_data_is_fresh():
    names = [flow.name for flow, _ in serve.SCHEDULES]
    assert names == [
        "sync-universe",
        "ingest-prices",
        "ingest-yields",
        "ingest-fundamentals",
        "score-universe",
    ]

    def when(cron):
        minute, hour, *_ = cron.split()
        return int(hour), int(minute)

    times = {flow.name: when(cron) for flow, cron in serve.SCHEDULES}
    for ingest in ("ingest-prices", "ingest-yields", "ingest-fundamentals"):
        assert times["score-universe"] > times[ingest]
    assert len(serve.deployments()) == len(serve.SCHEDULES)
