import httpx
import pytest
from prefect.testing.utilities import prefect_test_harness

from fin_core.db import migrate, repo
from fin_core.db.session import make_engine, session_scope
from fin_core.providers.fred import FredClient
from fin_flows import ingestion


@pytest.fixture
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'flows.db'}"
    migrate.upgrade(url)
    eng = make_engine(url)
    monkeypatch.setattr(ingestion, "make_engine", lambda: eng)
    return eng


def test_sync_universe_flow_fills_the_database(engine):
    with prefect_test_harness():
        n = ingestion.sync_universe_flow()
    assert n > 10
    with session_scope(engine) as s:
        assert len(repo.list_securities(s)) == n


def test_yields_flow_raises_when_the_run_fails_outright(engine, monkeypatch):
    monkeypatch.setattr(
        ingestion,
        "_fred",
        lambda: FredClient(
            api_key="k",
            client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429))),
        ),
    )
    with prefect_test_harness(), pytest.raises(RuntimeError, match="ingestion failed"):
        ingestion.ingest_yields_flow(["DGS10"])
