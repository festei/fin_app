import pytest
from prefect.testing.utilities import prefect_test_harness

from fin_application.errors import ChannelConfigError
from fin_application.runs import RunSummary
from fin_bootstrap import Container, Settings
from fin_flows import pipeline, scoring, serve
from fin_persistence import migrate


@pytest.fixture
def url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'scoring.db'}"
    migrate.upgrade(url)
    monkeypatch.delenv("FEATURE_TELEGRAM_ENABLED", raising=False)
    return url


def use_container(monkeypatch, url, **overrides):
    monkeypatch.setattr(
        scoring, "_container", lambda: Container(Settings(database_url=url), **overrides)
    )


class FakeRun:
    """Stands in for ``container.run_scoring`` and records how it was called."""

    def __init__(self, summary):
        self.summary, self.seen = summary, {}

    def execute(self, as_of=None, tickers=None):
        self.seen["tickers"] = tickers
        return self.summary


class FakeContainer:
    def __init__(self, run):
        self.run_scoring = run

    def dispose(self):
        pass


def test_flow_runs_the_real_scoring_run_on_an_empty_universe(url, monkeypatch):
    use_container(monkeypatch, url)
    with prefect_test_harness():
        out = scoring.score_universe_flow()
    assert out["source"] == "scoring" and out["status"] == "ok" and out["rows"] == 0


def test_flow_passes_the_tickers_through(url, monkeypatch):
    run = FakeRun(RunSummary("scoring", status="ok", rows=2, signals=1, alerts=1))
    monkeypatch.setattr(scoring, "_container", lambda: FakeContainer(run))
    with prefect_test_harness():
        out = scoring.score_universe_flow(["AAA"])
    assert run.seen["tickers"] == ["AAA"] and (out["signals"], out["alerts"]) == (1, 1)


def test_flow_fails_when_the_whole_run_failed(url, monkeypatch):
    run = FakeRun(RunSummary("scoring", status="failed", failed={"AAA": "boom"}))
    monkeypatch.setattr(scoring, "_container", lambda: FakeContainer(run))
    with prefect_test_harness(), pytest.raises(RuntimeError, match="scoring run failed"):
        scoring.score_universe_flow()


def test_flow_fails_loudly_on_an_invalid_channel_configuration(url, monkeypatch):
    monkeypatch.setenv("FEATURE_TELEGRAM_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    use_container(monkeypatch, url)
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
        "sandbox-step",
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
