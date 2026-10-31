"""Serve every scheduled flow: ``python -m fin_flows.serve`` (needs ``PREFECT_API_URL``).

Weekday schedules in UTC, after the US close. Order matters: prices and yields first, then
fundamentals, and scoring last so it sees the fresh data.
"""

from __future__ import annotations

from prefect import serve

from fin_flows.ingestion import (
    ingest_fundamentals_flow,
    ingest_prices_flow,
    ingest_yields_flow,
    sync_universe_flow,
)
from fin_flows.scoring import score_universe_flow

SCHEDULES = [
    (sync_universe_flow, "0 21 * * 1-5"),
    (ingest_prices_flow, "30 21 * * 1-5"),
    (ingest_yields_flow, "30 22 * * 1-5"),
    (ingest_fundamentals_flow, "0 23 * * 1-5"),
    (score_universe_flow, "30 23 * * 1-5"),
]


def deployments():
    return [flow.to_deployment("daily", cron=cron) for flow, cron in SCHEDULES]


if __name__ == "__main__":
    serve(*deployments())
