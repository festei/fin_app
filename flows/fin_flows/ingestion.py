"""Prefect flows for the slow data cadence: universe, prices, EDGAR fundamentals, FRED yields.

Each flow raises when its run failed outright, so Prefect marks it failed (and can alert).
Partial runs (some tickers/series failed) complete but are visible in ``ingestion_runs``.
The schedules for all flows live in ``fin_flows.serve``.

The flows only call use cases; the wiring (engine, providers, config) is the container's.
"""

from __future__ import annotations

from prefect import flow

from fin_bootstrap import Container
from fin_flows.common import check_summary

_check = check_summary


def _container() -> Container:  # indirection so tests can inject a container
    return Container.from_environ()


@flow(name="sync-universe")
def sync_universe_flow() -> int:
    container = _container()
    try:
        return container.sync_universe.execute()
    finally:
        container.dispose()


@flow(name="ingest-fundamentals")
def ingest_fundamentals_flow(tickers: list[str] | None = None) -> dict:
    container = _container()
    try:
        return _check(container.ingest_fundamentals.execute(tickers))
    finally:
        container.dispose()


@flow(name="ingest-yields")
def ingest_yields_flow(series_ids: list[str] | None = None) -> dict:
    container = _container()
    try:
        return _check(container.ingest_yields.execute(series_ids))
    finally:
        container.dispose()


@flow(name="ingest-prices")
def ingest_prices_flow(tickers: list[str] | None = None) -> dict:
    container = _container()
    try:
        return _check(container.ingest_prices.execute(tickers))
    finally:
        container.dispose()
