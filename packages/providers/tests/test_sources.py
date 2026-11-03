"""The adapters that turn vendor shapes into domain objects."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pandas as pd
from provider_fixtures import fixture_json  # noqa: F401 - pytest fixture

from fin_domain.market_data import Fact, PriceBar, YieldPoint
from fin_domain.shared import Ticker
from fin_providers import (
    EdgarClient,
    EdgarFundamentalsSource,
    FredClient,
    FredYieldSource,
    YahooPriceSource,
    bars_from_frame,
)


def edgar(handler) -> EdgarFundamentalsSource:
    client = EdgarClient(
        user_agent="tests tester@example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0,
    )
    return EdgarFundamentalsSource(client)


def test_the_ticker_directory_is_keyed_by_domain_tickers(fixture_json):
    source = edgar(
        lambda r: httpx.Response(200, json=fixture_json("company_tickers_synthetic.json"))
    )
    directory = source.ticker_directory()
    assert directory[Ticker("TEST")].cik == "0001000001"
    assert directory[Ticker("TEST")].name == "Test Corp"


def test_company_facts_become_domain_facts(fixture_json):
    source = edgar(lambda r: httpx.Response(200, json=fixture_json("companyfacts_synthetic.json")))
    facts = source.facts("1000001")
    assert facts and all(isinstance(f, Fact) for f in facts)
    revenue = {f.filed: f.value for f in facts if f.tag.endswith("/Revenues")}
    assert Decimal("1000") in revenue.values() and Decimal("900") in revenue.values()


def test_a_figure_that_breaks_a_domain_rule_is_skipped_not_fatal():
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"start": "2024-01-01", "end": "2024-12-31", "val": 5, "accn": "ok",
                             "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-01"},
                            {"start": "2025-01-01", "end": "2024-12-31", "val": 6, "accn": "bad",
                             "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-01"},
                        ]
                    }
                }
            }
        }
    }  # fmt: skip
    source = edgar(lambda r: httpx.Response(200, json=payload))
    assert [f.accession for f in source.facts("1")] == ["ok"]


def test_fred_observations_become_yield_points(fixture_json):
    client = FredClient(
        api_key="k",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=fixture_json("fred_dgs10_synthetic.json"))
            )
        ),
    )
    points = FredYieldSource(client).observations("DGS10", None)
    assert points == [
        YieldPoint(date(2025, 1, 2), Decimal("4.57")),
        YieldPoint(date(2025, 1, 3), Decimal("4.60")),
        YieldPoint(date(2025, 1, 6), Decimal("4.62")),
    ]


# --- price frames -------------------------------------------------------------------------------


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


def test_prices_are_converted_to_utc_and_rows_without_a_close_are_dropped():
    bars = bars_from_frame(
        frame([("2025-01-02", 100.0, 101.0, 1000), ("2025-01-03", 101.0, float("nan"), 5)])
    )
    assert bars == [
        PriceBar(
            ts=datetime(2025, 1, 2, 5, 0, tzinfo=UTC),  # midnight New York == 05:00 UTC
            open=100.0,
            high=100.0,
            low=100.0,
            close=101.0,
            volume=1000,
        )
    ]


def test_naive_timestamps_are_taken_as_utc_and_missing_values_become_none():
    f = pd.DataFrame(
        {"Date": [pd.Timestamp("2025-01-02")], "Open": [None], "Close": [3.0], "Volume": [None]}
    )
    (bar,) = bars_from_frame(f)
    assert bar.ts == datetime(2025, 1, 2, tzinfo=UTC)
    assert (bar.open, bar.high, bar.volume, bar.close) == (None, None, None, 3.0)


def test_an_empty_frame_gives_no_bars():
    assert bars_from_frame(pd.DataFrame()) == []


def test_the_yahoo_source_asks_the_vendor_with_a_plain_symbol_and_period():
    asked = []

    class Vendor:
        def history(self, ticker, period="max"):
            asked.append((ticker, period))
            return frame([("2025-01-02", 1.0, 2.0, 3)])

    bars = YahooPriceSource(Vendor()).history(Ticker("brk-b"), "1mo")
    assert asked == [("BRK-B", "1mo")] and bars[0].close == 2.0
