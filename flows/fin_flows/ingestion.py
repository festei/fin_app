"""Prefect flows for the slow data cadence: universe, EDGAR fundamentals, FRED yields.

Each flow raises when its run failed outright, so Prefect marks it failed (and can alert).
Partial runs (some tickers/series failed) complete but are visible in ``ingestion_runs``.
"""

from __future__ import annotations

from dataclasses import asdict

from prefect import flow, get_run_logger, serve

from fin_core.db.session import make_engine, session_scope
from fin_core.ingest import ingest_fundamentals, ingest_prices, ingest_yields
from fin_core.providers.edgar import EdgarClient
from fin_core.providers.fred import FredClient
from fin_core.providers.yfinance_provider import YFinancePriceProvider
from fin_core.universe import load_bond_series, sync_universe


def _edgar() -> EdgarClient:  # indirection so tests can inject a client
    return EdgarClient()


def _fred() -> FredClient:
    return FredClient()


def _prices() -> YFinancePriceProvider:
    return YFinancePriceProvider()


def _check(summary) -> dict:
    log = get_run_logger()
    log.info(
        "%s run %s: %d rows, %d failures",
        summary.source,
        summary.status,
        summary.rows,
        len(summary.failed),
    )
    if summary.rebackfilled:
        log.info("price history re-fetched after a restatement: %s", ", ".join(summary.rebackfilled))
    for key, reason in summary.failed.items():
        log.warning("%s failed: %s", key, reason)
    if summary.status == "failed":
        raise RuntimeError(f"{summary.source} ingestion failed: {summary.failed}")
    return asdict(summary)


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


if __name__ == "__main__":
    # Weekday schedules (UTC), after the US close. Requires a Prefect server (PREFECT_API_URL).
    serve(
        sync_universe_flow.to_deployment("daily", cron="0 21 * * 1-5"),
        ingest_prices_flow.to_deployment("daily", cron="30 21 * * 1-5"),
        ingest_yields_flow.to_deployment("daily", cron="30 22 * * 1-5"),
        ingest_fundamentals_flow.to_deployment("daily", cron="0 23 * * 1-5"),
    )
