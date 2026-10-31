import pytest
from prefect.testing.utilities import prefect_test_harness

from fin_core.db import migrate
from fin_core.db.session import make_engine
from fin_core.ingest import RunSummary
from fin_flows import pipeline, scoring, serve


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


def test_one_scheduled_pipeline_and_every_step_available_by_hand():
    assert [(f.name, cron) for f, cron in serve.SCHEDULED] == [("daily-pipeline", "30 21 * * 1-5")]
    assert [f.name for f in serve.MANUAL] == [name for name, _ in pipeline.steps()]
    assert len(serve.deployments()) == len(serve.SCHEDULED) + len(serve.MANUAL)


def test_pipeline_runs_ingestion_in_order_and_scoring_last():
    assert [n for n, _ in pipeline.steps()] == [
        "sync-universe",
        "ingest-prices",
        "ingest-yields",
        "ingest-fundamentals",
        "score-universe",
    ]


def test_pipeline_runs_every_step_even_after_a_failure_then_fails(monkeypatch):
    ran = []

    def ok(name):
        return lambda: ran.append(name) or {"status": "ok"}

    def boom():
        ran.append("ingest-prices")
        raise RuntimeError("prices down")

    monkeypatch.setattr(
        pipeline,
        "steps",
        lambda: [
            ("sync-universe", ok("sync-universe")),
            ("ingest-prices", boom),
            ("score-universe", ok("score-universe")),
        ],
    )
    with prefect_test_harness(), pytest.raises(RuntimeError, match="ingest-prices"):
        pipeline.daily_pipeline_flow()
    assert ran == ["sync-universe", "ingest-prices", "score-universe"]


def test_pipeline_returns_each_step_result_when_all_succeed(monkeypatch):
    monkeypatch.setattr(
        pipeline, "steps", lambda: [("a", lambda: {"n": 1}), ("b", lambda: {"n": 2})]
    )
    with prefect_test_harness():
        assert pipeline.daily_pipeline_flow() == {"a": {"n": 1}, "b": {"n": 2}}
