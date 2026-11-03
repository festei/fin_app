"""The ingestion use cases, against in-memory fakes: no database, no HTTP.

Ported from the scenarios that used to run through SQLite, httpx mocks and pandas frames."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fin_application.errors import AccessDeniedError, ProviderError, RateLimitError
from fin_application.ingestion import IngestFundamentals, IngestPrices, IngestYields, SyncUniverse
from fin_application.job_runs import JobRunTracker
from fin_application.ports import CompanyListing
from fin_domain.market_data import (
    CONCEPT_TAGS,
    BondSeries,
    Fact,
    PriceBar,
    SecurityUpdate,
    YieldPoint,
    known_as_of,
)
from fin_domain.shared import JobRun, RunStatus, Ticker
from fin_testkit import (
    FakeClock,
    FakeFundamentalsSource,
    FakePriceSource,
    FakeUniverse,
    FakeYieldSource,
    InMemoryFactRepository,
    InMemoryStore,
    InMemoryUnitOfWork,
    InMemoryYieldRepository,
    in_memory_uow_factory,
)

TEST, MISSING, SHY = Ticker("TEST"), Ticker("MISSING"), Ticker("SHY")
D0 = datetime(2025, 1, 2, 5, 0, tzinfo=UTC)


class World:
    def __init__(self):
        self.store = InMemoryStore()
        self.clock = FakeClock()
        self.uow_factory = in_memory_uow_factory(self.store)
        self.tracker = JobRunTracker(self.uow_factory, self.clock)
        with self.uow_factory() as uow:
            uow.securities.upsert(
                [
                    SecurityUpdate(TEST),  # a stock without CIK: resolved from the directory
                    SecurityUpdate(MISSING),  # a stock the SEC list does not know
                    SecurityUpdate(SHY, {"kind": "etf"}),  # ETFs have no fundamentals
                ]
            )

    @property
    def runs(self) -> list[JobRun]:
        return [self.store.job_runs[i] for i in sorted(self.store.job_runs)]


@pytest.fixture
def world():
    return World()


def fact(value, *, filed=date(2025, 2, 15), accession="a1", concept="Revenue"):
    return Fact(
        concept=concept,
        tag=CONCEPT_TAGS[concept][0],
        unit="USD",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        instant=False,
        fiscal_year=2024,
        fiscal_period="FY",
        form="10-K",
        filed=filed,
        accession=accession,
        value=Decimal(str(value)),
    )


def bar(day, close):
    return PriceBar(ts=D0 + timedelta(days=day), close=close)


# --- SyncUniverse -------------------------------------------------------------------------------


def test_syncing_the_universe_is_idempotent_and_does_not_wipe_a_resolved_cik():
    w = World()
    universe = FakeUniverse([SecurityUpdate(Ticker("IBM"), {"name": "IBM", "sector": "Tech"})])
    sync = SyncUniverse(w.uow_factory, universe)
    assert sync.execute() == 1
    with w.uow_factory() as uow:  # as EDGAR resolves it
        ibm = uow.securities.get(Ticker("IBM"))
        ibm.resolve_identity("0000051143", "ignored")
        uow.securities.save(ibm)
    assert sync.execute() == 1  # the daily sync: the universe file has no cik
    with w.uow_factory() as uow:
        ibm = uow.securities.get(Ticker("IBM"))
        assert ibm.cik == "0000051143"
        assert len(uow.securities.list()) == 4  # TEST, MISSING, SHY, IBM


# --- IngestFundamentals -------------------------------------------------------------------------


def edgar(w, **kw):
    source = FakeFundamentalsSource(
        directory={TEST: CompanyListing("0001000001", "Test Corp")},
        facts={"0001000001": [fact(1000), fact(50, concept="NetIncome", accession="a2")]},
        **kw,
    )
    return source, IngestFundamentals(w.uow_factory, w.tracker, source)


def test_fundamentals_end_to_end_is_partial_when_one_ticker_is_unknown(world):
    source, ingest = edgar(world)
    summary = ingest.execute()

    assert summary.status == "partial"
    assert list(summary.failed) == ["MISSING"]
    assert summary.rows == 2
    assert source.fact_calls == ["0001000001"]  # only TEST was fetched; the ETF was skipped
    with world.uow_factory() as uow:
        test = uow.securities.get(TEST)
        assert (test.cik, test.name) == ("0001000001", "Test Corp")
        assert len(uow.facts.filed_up_to(TEST, date(2030, 1, 1))) == 2
    (run,) = world.runs
    assert (run.source, run.status, run.rows) == ("edgar", RunStatus.PARTIAL, 2)
    assert run.finished_at is not None and "MISSING" in run.error


def test_fundamentals_rerun_is_idempotent_and_ticker_case_does_not_matter(world):
    _, ingest = edgar(world)
    ingest.execute(["TEST"])
    second = ingest.execute(["test"])
    with world.uow_factory() as uow:
        assert len(uow.facts.filed_up_to(TEST, date(2030, 1, 1))) == 2
    assert second.status == "ok"


def test_a_restatement_stays_invisible_until_it_is_filed(world):
    source = FakeFundamentalsSource(
        directory={TEST: CompanyListing("0001000001", "Test Corp")},
        facts={
            "0001000001": [
                fact(1000, filed=date(2024, 2, 1), accession="orig"),
                fact(900, filed=date(2024, 8, 1), accession="rest"),
            ]
        },
    )
    IngestFundamentals(world.uow_factory, world.tracker, source).execute(["TEST"])
    with world.uow_factory() as uow:
        stored = uow.facts.filed_up_to(TEST, date(2030, 1, 1))
    assert [f.value for f in known_as_of(stored, date(2024, 3, 1))] == [Decimal("1000")]
    assert [f.value for f in known_as_of(stored, date(2024, 9, 1))] == [Decimal("900")]


def test_a_rate_limit_aborts_the_run(world):
    with world.uow_factory() as uow:
        uow.securities.upsert([SecurityUpdate(Ticker("AAA"), {"cik": "1"})])
        uow.securities.upsert([SecurityUpdate(Ticker("BBB"), {"cik": "2"})])
    source = FakeFundamentalsSource(
        directory={TEST: CompanyListing("0001000001", "Test Corp")},
        facts={"1": RateLimitError("EDGAR: HTTP 429 (rate limited)")},
    )
    summary = IngestFundamentals(world.uow_factory, world.tracker, source).execute()
    assert summary.status == "failed"
    assert "rate limited" in summary.failed["AAA"]
    assert source.fact_calls == ["1"]  # BBB (and everything after) was never asked
    assert world.runs[0].status is RunStatus.FAILED


def test_access_denied_aborts_with_the_providers_reason(world):
    reason = "EDGAR: HTTP 403 access denied: check the SEC_USER_AGENT contact details"
    source = FakeFundamentalsSource(
        directory={TEST: CompanyListing("0001000001", "Test Corp")},
        facts={"0001000001": AccessDeniedError(reason)},
    )
    summary = IngestFundamentals(world.uow_factory, world.tracker, source).execute()
    assert summary.status == "failed" and summary.failed["TEST"] == reason


def test_an_unavailable_directory_fails_cleanly(world):
    source = FakeFundamentalsSource(directory=ProviderError("EDGAR: HTTP 500"))
    summary = IngestFundamentals(world.uow_factory, world.tracker, source).execute()
    assert summary.status == "failed" and list(summary.failed) == ["*"]
    assert world.runs[0].status is RunStatus.FAILED


class FlakyUnitOfWork(InMemoryUnitOfWork):
    """A unit of work whose repositories fail on demand, to show the failure stays isolated."""

    fail_facts_for: Ticker | None = None
    fail_yields_for: str | None = None
    fail_securities_list = False

    def __enter__(self):
        super().__enter__()
        outer = self

        class Facts(InMemoryFactRepository):
            def add(self, ticker, facts):
                if ticker == outer.fail_facts_for:
                    raise OSError("connection dropped")
                return super().add(ticker, facts)

        class Yields(InMemoryYieldRepository):
            def upsert(self, series_id, points):
                if series_id == outer.fail_yields_for:
                    raise OSError("disk full")
                return super().upsert(series_id, points)

        class Securities(type(self.securities)):
            def list(self, **kw):
                if outer.fail_securities_list:
                    raise RuntimeError("database went away")
                return super().list(**kw)

        self.facts = Facts(self._work.facts, self._work.securities)
        self.yields = Yields(self._work.yields)
        self.securities = Securities(self._work.securities)
        return self


def flaky_world(**flags):
    w = World()

    def factory():
        uow = FlakyUnitOfWork(w.store)
        for name, value in flags.items():
            setattr(uow, name, value)
        return uow

    w.uow_factory = factory
    w.tracker = JobRunTracker(factory, w.clock)
    return w


def test_a_storage_error_on_one_ticker_is_isolated_and_the_run_is_not_ok():
    w = flaky_world(fail_facts_for=TEST)
    with w.uow_factory() as uow:
        uow.securities.upsert([SecurityUpdate(Ticker("TEST2"), {"cik": "0001000002"})])
    source = FakeFundamentalsSource(
        directory={TEST: CompanyListing("0001000001", "Test Corp")},
        facts={"0001000001": [fact(1)], "0001000002": [fact(2, accession="b")]},
    )
    summary = IngestFundamentals(w.uow_factory, w.tracker, source).execute(["TEST", "TEST2"])

    assert summary.status == "partial"
    assert "OSError" in summary.failed["TEST"] and "TEST2" not in summary.failed
    assert summary.rows == 1  # the other ticker still ran
    assert w.runs[-1].status is RunStatus.PARTIAL


# --- IngestYields -------------------------------------------------------------------------------

JAN2, JAN3, JAN6 = date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)
DGS10 = [
    YieldPoint(JAN2, Decimal("4.57")),
    YieldPoint(JAN3, Decimal("4.60")),
    YieldPoint(JAN6, Decimal("4.62")),
]


def test_yields_load_fully_then_incrementally_with_an_overlap(world):
    source = FakeYieldSource({"DGS10": DGS10})
    ingest = IngestYields(world.uow_factory, world.tracker, source, FakeUniverse())

    first = ingest.execute(["DGS10"])
    assert first.status == "ok" and first.rows == 3
    assert source.calls[0] == ("DGS10", None)  # first run: full history

    ingest.execute(["DGS10"])
    assert source.calls[1] == ("DGS10", date(2024, 12, 30))  # last obs 2025-01-06 minus 7 days

    with world.uow_factory() as uow:
        assert len(world.store.yields["DGS10"]) == 3  # the re-fetched overlap did not duplicate
        assert uow.yields.latest("DGS10").value == Decimal("4.62")


def test_an_explicit_start_wins(world):
    source = FakeYieldSource({"DGS10": DGS10})
    IngestYields(world.uow_factory, world.tracker, source, FakeUniverse()).execute(
        ["DGS10"], start=date(2020, 1, 1)
    )
    assert source.calls == [("DGS10", date(2020, 1, 1))]


def test_one_bad_series_makes_the_run_partial(world):
    source = FakeYieldSource({"DGS10": DGS10, "BAD": ProviderError("FRED BAD: HTTP 400")})
    summary = IngestYields(world.uow_factory, world.tracker, source, FakeUniverse()).execute(
        ["DGS10", "BAD"]
    )
    assert summary.status == "partial" and list(summary.failed) == ["BAD"]


def test_a_rate_limit_aborts_the_remaining_series(world):
    source = FakeYieldSource({"DGS10": RateLimitError("FRED: rate limited (HTTP 429)")})
    summary = IngestYields(world.uow_factory, world.tracker, source, FakeUniverse()).execute(
        ["DGS10", "DGS2"]
    )
    assert summary.status == "failed" and list(summary.failed) == ["DGS10"]
    assert [c[0] for c in source.calls] == ["DGS10"]


def test_without_ids_the_configured_bond_series_are_fetched(world):
    universe = FakeUniverse(series=[BondSeries("DGS2", "yield"), BondSeries("T10Y2Y", "spread")])
    source = FakeYieldSource({})
    IngestYields(world.uow_factory, world.tracker, source, universe).execute()
    assert [c[0] for c in source.calls] == ["DGS2", "T10Y2Y"]


def test_a_storage_error_on_one_series_is_isolated():
    w = flaky_world(fail_yields_for="DGS2")
    source = FakeYieldSource({"DGS2": DGS10, "DGS10": DGS10})
    summary = IngestYields(w.uow_factory, w.tracker, source, FakeUniverse()).execute(
        ["DGS2", "DGS10"]
    )
    assert summary.status == "partial" and list(summary.failed) == ["DGS2"]
    assert "DGS10" in w.store.yields


# --- IngestPrices -------------------------------------------------------------------------------


def test_prices_backfill_first_then_use_the_refresh_window_and_stay_idempotent(world):
    source = FakePriceSource({TEST: [bar(0, 2.0)]})
    ingest = IngestPrices(world.uow_factory, world.tracker, source)
    ingest.execute(["TEST"])
    assert source.calls[-1] == (TEST, "max")  # nothing stored yet: back-fill
    ingest.execute(["TEST"])
    assert source.calls[-1] == (TEST, "1mo")  # bars exist: short refresh window
    assert len(world.store.prices[TEST]) == 1


def test_empty_and_failing_tickers_are_isolated(world):
    source = FakePriceSource({TEST: [bar(0, 2.0)], SHY: ProviderError("boom")})  # MISSING: empty
    summary = IngestPrices(world.uow_factory, world.tracker, source).execute()
    assert summary.status == "partial"
    assert summary.failed == {"MISSING": "no price data returned", "SHY": "boom"}
    assert summary.rows == 1


def test_an_unexpected_exception_does_not_stop_the_run(world):
    source = FakePriceSource({TEST: [bar(0, 2.0)], MISSING: KeyError("x")})
    summary = IngestPrices(world.uow_factory, world.tracker, source).execute(["TEST", "MISSING"])
    assert summary.status == "partial" and "KeyError" in summary.failed["MISSING"]


def test_a_price_rate_limit_aborts(world):
    source = FakePriceSource({MISSING: RateLimitError("slow down")})
    summary = IngestPrices(world.uow_factory, world.tracker, source).execute()
    assert summary.status == "failed" and "MISSING" in summary.failed
    assert world.runs[-1].source == "prices"
    assert [t for t, _ in source.calls] == [MISSING]  # sorted: MISSING first, then it stopped


def stored_closes(world, ticker=TEST):
    return {ts.date().isoformat(): b.close for ts, b in world.store.prices[ticker].items()}


def test_a_split_adjusted_refresh_triggers_a_full_rebackfill(world):
    first = FakePriceSource({TEST: lambda period: [bar(-2, 98.0), bar(0, 100.0)]})
    IngestPrices(world.uow_factory, world.tracker, first).execute(["TEST"])
    assert stored_closes(world) == {"2024-12-31": 98.0, "2025-01-02": 100.0}

    # a 2:1 split: the source now reports every historical close halved
    def after_split(period):
        if period == "1mo":
            return [bar(0, 50.0), bar(1, 51.0)]
        return [bar(-2, 49.0), bar(0, 50.0), bar(1, 51.0)]

    second = FakePriceSource({TEST: after_split})
    summary = IngestPrices(world.uow_factory, world.tracker, second).execute(["TEST"])
    assert summary.rebackfilled == ["TEST"]
    assert [p for _, p in second.calls] == ["1mo", "max"]
    # the older bar moved to the new basis too: no 98 -> 50 cliff left in the series
    assert stored_closes(world) == {"2024-12-31": 49.0, "2025-01-02": 50.0, "2025-01-03": 51.0}


def test_unchanged_prices_only_use_the_cheap_refresh_window(world):
    rows = [bar(0, 100.0), bar(1, 101.0)]
    source = FakePriceSource({TEST: lambda period: rows})
    ingest = IngestPrices(world.uow_factory, world.tracker, source)
    ingest.execute(["TEST"])
    source.calls.clear()
    summary = ingest.execute(["TEST"])
    assert summary.rebackfilled == [] and [p for _, p in source.calls] == ["1mo"]


def test_a_new_bar_alone_is_not_mistaken_for_a_restatement(world):
    def history(period):
        return [bar(0, 100.0)] if period == "max" else [bar(0, 100.0), bar(1, 103.0)]

    source = FakePriceSource({TEST: history})
    ingest = IngestPrices(world.uow_factory, world.tracker, source)
    ingest.execute(["TEST"])
    summary = ingest.execute(["TEST"])
    assert summary.rebackfilled == []
    assert stored_closes(world)["2025-01-03"] == 103.0


# --- run bookkeeping ----------------------------------------------------------------------------


def test_a_crash_inside_a_job_is_recorded_as_failed_and_reraised():
    w = flaky_world(fail_securities_list=True)
    with pytest.raises(RuntimeError, match="went away"):
        IngestPrices(w.uow_factory, w.tracker, FakePriceSource()).execute()
    run = w.runs[-1]
    assert run.status is RunStatus.FAILED  # previously this was once written as "ok"
    assert run.finished_at is not None and "went away" in run.error


def test_stale_running_rows_are_marked_failed_by_the_next_run(world):
    now = world.clock.now()
    with world.uow_factory() as uow:
        uow.job_runs.add(JobRun.start("edgar", now - timedelta(hours=10)))
        uow.job_runs.add(JobRun.start("prices", now - timedelta(minutes=1)))
    IngestYields(world.uow_factory, world.tracker, FakeYieldSource(), FakeUniverse()).execute([])
    by_source = {r.source: r for r in world.runs}
    assert by_source["edgar"].status is RunStatus.FAILED and "stale" in by_source["edgar"].error
    assert by_source["prices"].status is RunStatus.RUNNING  # a recent run may still be alive
