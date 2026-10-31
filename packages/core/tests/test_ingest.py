from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from fin_core import ingest
from fin_core.db import migrate, repo
from fin_core.db.models import BondYield, FundamentalFact, IngestionRun
from fin_core.db.session import make_engine, session_scope
from fin_core.providers.edgar import EdgarClient
from fin_core.providers.fred import FredClient


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'ingest.db'}"
    migrate.upgrade(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        repo.upsert_securities(
            s,
            [
                {"ticker": "TEST"},  # stock without CIK: resolved from the ticker map
                {"ticker": "MISSING"},  # stock the SEC list does not know
                {"ticker": "SHY", "kind": "etf"},  # ETFs have no fundamentals: skipped
            ],
        )
    yield eng
    eng.dispose()


def edgar_with(handler) -> EdgarClient:
    return EdgarClient(
        user_agent="tests tester@example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0,
    )


def edgar_ok(fixture_json, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=fixture_json("company_tickers_synthetic.json"))
        return httpx.Response(200, json=fixture_json("companyfacts_synthetic.json"))

    return edgar_with(handler)


def runs(engine):
    with session_scope(engine) as s:
        return list(s.scalars(select(IngestionRun).order_by(IngestionRun.id)))


def test_fundamentals_end_to_end_partial_when_one_ticker_unknown(engine, fixture_json):
    calls: list[str] = []
    summary = ingest.ingest_fundamentals(engine, edgar_ok(fixture_json, calls))

    assert summary.status == "partial"
    assert list(summary.failed) == ["MISSING"]
    assert summary.rows > 0
    assert not any("SHY" in c for c in calls)
    assert sum("companyfacts" in c for c in calls) == 1  # only TEST was fetched

    with session_scope(engine) as s:
        test = repo.get_security(s, "TEST")
        assert test.cik == "0001000001" and test.name == "Test Corp"
        assert s.query(FundamentalFact).filter_by(security_id=test.id).count() == summary.rows
    (run,) = runs(engine)
    assert (run.source, run.status, run.rows) == ("edgar", "partial", summary.rows)
    assert run.finished_at is not None and "MISSING" in run.error


def test_fundamentals_rerun_is_idempotent(engine, fixture_json):
    ingest.ingest_fundamentals(engine, edgar_ok(fixture_json), tickers=["TEST"])
    with session_scope(engine) as s:
        first = s.query(FundamentalFact).count()
    second = ingest.ingest_fundamentals(engine, edgar_ok(fixture_json), tickers=["test"])
    with session_scope(engine) as s:
        assert s.query(FundamentalFact).count() == first
    assert second.status == "ok"


def test_fundamentals_point_in_time_through_the_pipeline(engine, fixture_json):
    ingest.ingest_fundamentals(engine, edgar_ok(fixture_json), tickers=["TEST"])
    with session_scope(engine) as s:
        sid = repo.get_security(s, "TEST").id

        def revenue(as_of):
            return sorted(
                f.value
                for f in repo.facts_as_of(s, sid, as_of, ["Revenue"])
                if f.tag.endswith("Revenues")
            )

        assert revenue(date(2024, 3, 1)) == [Decimal("1000")]
        assert revenue(date(2024, 9, 1)) == [Decimal("900")]


def test_rate_limit_aborts_the_run(engine, fixture_json):
    def handler(request):
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=fixture_json("company_tickers_synthetic.json"))
        return httpx.Response(429)

    summary = ingest.ingest_fundamentals(engine, edgar_with(handler))
    assert summary.status == "failed"
    assert "rate limited" in summary.failed["TEST"]
    assert runs(engine)[0].status == "failed"


def test_ticker_map_outage_fails_cleanly(engine):
    summary = ingest.ingest_fundamentals(engine, edgar_with(lambda r: httpx.Response(500)))
    assert summary.status == "failed" and "*" in summary.failed
    assert runs(engine)[0].status == "failed"


def fred_with(handler) -> FredClient:
    return FredClient(api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_yields_end_to_end_then_incremental_with_overlap(engine, fixture_json):
    params: list[dict] = []

    def handler(request):
        params.append(dict(request.url.params))
        return httpx.Response(200, json=fixture_json("fred_dgs10_synthetic.json"))

    first = ingest.ingest_yields(engine, fred_with(handler), ["DGS10"])
    assert first.status == "ok" and first.rows == 3
    assert "observation_start" not in params[0]  # first run: full history

    ingest.ingest_yields(engine, fred_with(handler), ["DGS10"])
    assert params[1]["observation_start"] == "2024-12-30"  # last obs 2025-01-06 minus 7 days

    with session_scope(engine) as s:
        assert s.query(BondYield).count() == 3  # re-fetched overlap did not duplicate
        assert repo.latest_yield(s, "DGS10").value == Decimal("4.62")


def test_yields_one_bad_series_is_partial(engine, fixture_json):
    def handler(request):
        if request.url.params["series_id"] == "BAD":
            return httpx.Response(400, json={"error_message": "series does not exist"})
        return httpx.Response(200, json=fixture_json("fred_dgs10_synthetic.json"))

    summary = ingest.ingest_yields(engine, fred_with(handler), ["DGS10", "BAD"])
    assert summary.status == "partial" and list(summary.failed) == ["BAD"]


def test_yields_rate_limit_aborts(engine):
    summary = ingest.ingest_yields(
        engine, fred_with(lambda r: httpx.Response(429)), ["DGS10", "DGS2"]
    )
    assert summary.status == "failed" and list(summary.failed) == ["DGS10"]
