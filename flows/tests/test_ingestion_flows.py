import httpx
import pytest
from prefect.testing.utilities import prefect_test_harness

from fin_bootstrap import Container, Settings
from fin_flows import ingestion
from fin_persistence import migrate, repo
from fin_persistence.session import make_engine, session_scope
from fin_providers import FredClient, FredYieldSource


@pytest.fixture
def url(tmp_path):
    url = f"sqlite:///{tmp_path / 'flows.db'}"
    migrate.upgrade(url)
    return url


def use_container(monkeypatch, url, **overrides):
    monkeypatch.setattr(
        ingestion, "_container", lambda: Container(Settings(database_url=url), **overrides)
    )


def test_sync_universe_flow_fills_the_database(url, monkeypatch):
    use_container(monkeypatch, url)
    with prefect_test_harness():
        n = ingestion.sync_universe_flow()
    assert n > 10
    engine = make_engine(url)
    with session_scope(engine) as s:
        assert len(repo.list_securities(s)) == n


def test_yields_flow_raises_when_the_run_fails_outright(url, monkeypatch):
    rate_limited = FredClient(
        api_key="k",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429))),
    )
    use_container(monkeypatch, url, yield_source=FredYieldSource(rate_limited))
    with prefect_test_harness(), pytest.raises(RuntimeError, match="run failed"):
        ingestion.ingest_yields_flow(["DGS10"])
