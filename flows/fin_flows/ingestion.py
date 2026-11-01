"""Prefect flows for the slow data cadence: universe, prices, EDGAR fundamentals, FRED yields.

Each flow raises when its run failed outright, so Prefect marks it failed (and can alert).
Partial runs (some tickers/series failed) complete but are visible in ``ingestion_runs``.
The schedules for all flows live in ``fin_flows.serve``.
"""

from __future__ import annotations

from prefect import flow

from fin_core.db.session import make_engine, session_scope
from fin_core.ingest import ingest_fundamentals, ingest_prices, ingest_yields
from fin_core.providers.edgar import EdgarClient
from fin_core.providers.fred import FredClient
from fin_core.providers.yfinance_provider import YFinancePriceProvider
from fin_core.universe import load_bond_series, sync_universe
from fin_flows.common import check_summary


def _edgar() -> EdgarClient:  # indirection so tests can inject a client
    return EdgarClient()


def _fred() -> FredClient:
    return FredClient()


def _prices() -> YFinancePriceProvider:
    return YFinancePriceProvider()


_check = check_summary


@flow(name="sync-universe")
def sync_universe_flow() -> int:
    engine = make_engine()
    with session_scope(engine) as s:
        return sync_universe(s)


@flow(name="ingest-fundamentals")
def ingest_fundamentals_flow(tickers: list[str] | None = None) -> dict:
    return _check(ingest_fundamentals(make_engine(), _edgar(), tickers))


@flow(name="ingest-yields")
def ingest_yields_flow(series_ids: list[str] | None = None) -> dict:
    ids = series_ids or [x["id"] for x in load_bond_series()]
    return _check(ingest_yields(make_engine(), _fred(), ids))


@flow(name="ingest-prices")
def ingest_prices_flow(tickers: list[str] | None = None) -> dict:
    return _check(ingest_prices(make_engine(), _prices(), tickers))
