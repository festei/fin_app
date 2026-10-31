from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from fin_core import ingest
from fin_core.db import repo
from fin_core.db.models import BondYield, FundamentalFact, IngestionRun, PriceBar
from fin_core.db.session import session_scope
from fin_core.providers.base import ProviderError, RateLimitError
from fin_core.providers.edgar import EdgarClient
from fin_core.providers.fred import FredClient


@pytest.fixture
def engine(raw_engine):
    with session_scope(raw_engine) as s:
        repo.upsert_securities(
            s,
            [
                {"ticker": "TEST"},  # stock without CIK: resolved from the ticker map
                {"ticker": "MISSING"},  # stock the SEC list does not know
                {"ticker": "SHY", "kind": "etf"},  # ETFs have no fundamentals: skipped
            ],
        )
    return raw_engine


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


# --- prices ------------------------------------------------------------------------------


class FakePrices:
    """Records the period requested per ticker; behaviour per ticker is configurable."""

    def __init__(self, frames=None, errors=None):
        self.frames, self.errors, self.periods = frames or {}, errors or {}, {}

    def history(self, ticker, period="max"):
        self.periods[ticker] = period
        if ticker in self.errors:
            raise self.errors[ticker]
        return self.frames.get(ticker, pd.DataFrame())

    def last_price(self, ticker):  # pragma: no cover - protocol completeness
        return None


def frame(rows, tz="America/New_York"):
    idx = pd.DatetimeIndex([r[0] for r in rows], name="Date", tz=tz)
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[1] for r in rows],
            "Low": [r[1] for r in rows],
            "Close": [r[2] for r in rows],
            "Volume": [r[3] for r in rows],
        },
        index=idx,
    ).reset_index()


def test_prices_stored_in_utc_and_nan_rows_dropped(engine):
    f = frame([("2025-01-02", 100.0, 101.0, 1000), ("2025-01-03", 101.0, float("nan"), 5)])
    summary = ingest.ingest_prices(engine, FakePrices({"TEST": f}), ["TEST"])
    assert summary.status == "ok" and summary.rows == 1
    with session_scope(engine) as s:
        sid = repo.get_security(s, "TEST").id
        bar = repo.latest_price(s, sid)
        assert bar.close == 101.0 and bar.volume == 1000
        # midnight New York == 05:00 UTC
        assert (bar.ts.hour, bar.ts.minute) == (5, 0)


def test_prices_backfill_first_then_refresh_window_and_idempotent(engine):
    provider = FakePrices({"TEST": frame([("2025-01-02", 1.0, 2.0, 10)])})
    ingest.ingest_prices(engine, provider, ["TEST"])
    assert provider.periods["TEST"] == "max"  # nothing stored yet: back-fill
    ingest.ingest_prices(engine, provider, ["TEST"])
    assert provider.periods["TEST"] == "1mo"  # bars exist: short refresh window
    with session_scope(engine) as s:
        assert s.query(PriceBar).count() == 1


def test_prices_empty_and_failing_tickers_are_isolated(engine):
    provider = FakePrices(
        frames={"TEST": frame([("2025-01-02", 1.0, 2.0, 10)])},  # MISSING returns empty
        errors={"SHY": ProviderError("boom")},
    )
    summary = ingest.ingest_prices(engine, provider)
    assert summary.status == "partial"
    assert summary.failed == {"MISSING": "no price data returned", "SHY": "boom"}
    assert summary.rows == 1


def test_prices_unexpected_exception_does_not_stop_the_run(engine):
    provider = FakePrices(
        frames={"TEST": frame([("2025-01-02", 1.0, 2.0, 10)])}, errors={"MISSING": KeyError("x")}
    )
    summary = ingest.ingest_prices(engine, provider, ["TEST", "MISSING"])
    assert summary.status == "partial" and "KeyError" in summary.failed["MISSING"]


def test_prices_rate_limit_aborts(engine):
    provider = FakePrices(errors={"MISSING": RateLimitError("slow down")})
    summary = ingest.ingest_prices(engine, provider)
    assert summary.status == "failed" and "MISSING" in summary.failed
    assert runs(engine)[-1].source == "prices"


# --- run bookkeeping (review: a crash must never be recorded as ok) --------------------------


def test_database_error_on_one_ticker_is_isolated_and_the_run_is_not_ok(
    engine, fixture_json, monkeypatch
):
    with session_scope(engine) as s:
        repo.upsert_securities(s, [{"ticker": "TEST2", "cik": "0001000002"}])
        broken_id = repo.get_security(s, "TEST").id
    real = repo.upsert_facts

    def flaky(session, security_id, facts):
        if security_id == broken_id:
            raise OperationalError("INSERT", {}, Exception("connection dropped"))
        return real(session, security_id, facts)

    monkeypatch.setattr(ingest.repo, "upsert_facts", flaky)
    summary = ingest.ingest_fundamentals(engine, edgar_ok(fixture_json), ["TEST", "TEST2"])

    assert summary.status == "partial"
    assert "OperationalError" in summary.failed["TEST"]
    assert "TEST2" not in summary.failed and summary.rows > 0  # the other ticker still ran
    with session_scope(engine) as s:
        assert (
            s.query(FundamentalFact).filter_by(security_id=repo.get_security(s, "TEST2").id).count()
            > 0
        )
    assert runs(engine)[-1].status == "partial"


def test_database_error_on_one_series_is_isolated(engine, fixture_json, monkeypatch):
    real = repo.upsert_yields

    def flaky(session, series_id, points):
        if series_id == "DGS2":
            raise OperationalError("INSERT", {}, Exception("boom"))
        return real(session, series_id, points)

    monkeypatch.setattr(ingest.repo, "upsert_yields", flaky)
    fred = fred_with(lambda r: httpx.Response(200, json=fixture_json("fred_dgs10_synthetic.json")))
    summary = ingest.ingest_yields(engine, fred, ["DGS2", "DGS10"])
    assert summary.status == "partial" and list(summary.failed) == ["DGS2"]
    with session_scope(engine) as s:
        assert repo.latest_yield(s, "DGS10") is not None


def test_a_crash_inside_a_job_is_recorded_as_failed_and_reraised(engine, monkeypatch):
    def boom(session, **kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(ingest.repo, "list_securities", boom)
    with pytest.raises(RuntimeError, match="went away"):
        ingest.ingest_prices(engine, FakePrices())
    run = runs(engine)[-1]
    assert run.status == "failed"  # previously this was written as "ok"
    assert run.finished_at is not None and "went away" in run.error


def test_stale_running_rows_are_marked_failed_by_the_next_run(engine):
    now = datetime.now(UTC)
    with session_scope(engine) as s:
        old, recent = now - timedelta(hours=10), now - timedelta(minutes=1)
        s.add(IngestionRun(source="edgar", status="running", started_at=old))
        s.add(IngestionRun(source="prices", status="running", started_at=recent))
    ingest.ingest_yields(
        engine, fred_with(lambda r: httpx.Response(200, json={"observations": []})), []
    )
    by_source = {r.source: r for r in runs(engine)}
    assert by_source["edgar"].status == "failed" and "stale" in by_source["edgar"].error
    assert by_source["prices"].status == "running"  # a recent run may still be alive


def test_edgar_access_denied_aborts_the_run_with_a_clear_reason(engine, fixture_json):
    def handler(request):
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=fixture_json("company_tickers_synthetic.json"))
        return httpx.Response(403, text="Undeclared Automated Tool")

    summary = ingest.ingest_fundamentals(engine, edgar_with(handler))
    assert summary.status == "failed"
    assert "SEC_USER_AGENT" in summary.failed["TEST"]


# --- price restatements (review: adjusted history must not get a discontinuity) -------------


class PeriodPrices:
    """Returns a different frame per requested period and records the calls."""

    def __init__(self, by_period):
        self.by_period, self.calls = by_period, []

    def history(self, ticker, period="max"):
        self.calls.append((ticker, period))
        return self.by_period[period]

    def last_price(self, ticker):  # pragma: no cover
        return None


def closes(engine, ticker="TEST"):
    with session_scope(engine) as s:
        sid = repo.get_security(s, ticker).id
        return {
            b.ts.date().isoformat(): b.close for b in s.query(PriceBar).filter_by(security_id=sid)
        }


def test_split_adjusted_refresh_triggers_a_full_rebackfill(engine):
    first = PeriodPrices(
        {"max": frame([("2024-12-31", 98.0, 98.0, 1), ("2025-01-02", 100.0, 100.0, 1)])}
    )
    ingest.ingest_prices(engine, first, ["TEST"])
    assert closes(engine) == {"2024-12-31": 98.0, "2025-01-02": 100.0}

    # a 2:1 split: the source now reports every historical close halved
    second = PeriodPrices(
        {
            "1mo": frame([("2025-01-02", 50.0, 50.0, 2), ("2025-01-03", 51.0, 51.0, 2)]),
            "max": frame(
                [
                    ("2024-12-31", 49.0, 49.0, 2),
                    ("2025-01-02", 50.0, 50.0, 2),
                    ("2025-01-03", 51.0, 51.0, 2),
                ]
            ),
        }
    )
    summary = ingest.ingest_prices(engine, second, ["TEST"])
    assert summary.rebackfilled == ["TEST"]
    assert [p for _, p in second.calls] == ["1mo", "max"]
    # the older bar moved to the new basis too: no 98 -> 50 cliff left in the series
    assert closes(engine) == {"2024-12-31": 49.0, "2025-01-02": 50.0, "2025-01-03": 51.0}


def test_unchanged_prices_only_use_the_cheap_refresh_window(engine):
    rows = [("2025-01-02", 100.0, 100.0, 1), ("2025-01-03", 101.0, 101.0, 1)]
    provider = PeriodPrices({"max": frame(rows), "1mo": frame(rows)})
    ingest.ingest_prices(engine, provider, ["TEST"])
    provider.calls.clear()
    summary = ingest.ingest_prices(engine, provider, ["TEST"])
    assert summary.rebackfilled == [] and [p for _, p in provider.calls] == ["1mo"]


def test_a_new_bar_alone_is_not_mistaken_for_a_restatement(engine):
    provider = PeriodPrices(
        {
            "max": frame([("2025-01-02", 100.0, 100.0, 1)]),
            "1mo": frame([("2025-01-02", 100.0, 100.0, 1), ("2025-01-03", 103.0, 103.0, 1)]),
        }
    )
    ingest.ingest_prices(engine, provider, ["TEST"])
    summary = ingest.ingest_prices(engine, provider, ["TEST"])
    assert summary.rebackfilled == []
    assert closes(engine)["2025-01-03"] == 103.0
