"""Serve every flow: ``python -m fin_flows.serve`` (needs ``PREFECT_API_URL``).

One scheduled deployment, the chained daily pipeline (weekdays, after the US close, UTC):
ingestion first, scoring last. Each step is also deployed without a schedule so it can be
triggered by hand from the Prefect UI.
"""

from __future__ import annotations

from prefect import serve

from fin_flows.ingestion import (
    ingest_fundamentals_flow,
    ingest_prices_flow,
    ingest_yields_flow,
    sync_universe_flow,
)
from fin_flows.pipeline import daily_pipeline_flow
from fin_flows.scoring import score_universe_flow

SCHEDULED = [(daily_pipeline_flow, "30 21 * * 1-5")]
MANUAL = [
    sync_universe_flow,
    ingest_prices_flow,
    ingest_yields_flow,
    ingest_fundamentals_flow,
    score_universe_flow,
]


def deployments():
    scheduled = [flow.to_deployment("daily", cron=cron) for flow, cron in SCHEDULED]
    manual = [flow.to_deployment("manual") for flow in MANUAL]
    return scheduled + manual


if __name__ == "__main__":
    serve(*deployments())
