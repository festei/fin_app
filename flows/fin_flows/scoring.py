"""Batch scoring flow: run the philosophy graph once per security.

Skeleton for phase 0. In phase 1 the metrics come from Postgres and results are stored.
"""

from __future__ import annotations

from prefect import flow, task

from fin_core.graph import build_graph, load_config
from fin_core.models import SecurityMetrics


@task(retries=2, retry_delay_seconds=5)
def score_one(metrics: SecurityMetrics, previous_conviction: float | None = None) -> dict:
    out = build_graph().invoke(
        {
            "metrics": metrics,
            "config": load_config(),
            "previous_conviction": previous_conviction,
        }
    )
    return {
        "ticker": metrics.ticker,
        "conviction": out["conviction"],
        "signal": out.get("signal"),
    }


@flow(name="score-universe")
def score_universe(universe: list[SecurityMetrics]) -> list[dict]:
    futures = [score_one.submit(m) for m in universe]
    return [f.result() for f in futures]


if __name__ == "__main__":
    # Serve on a schedule (every weekday at 22:00 UTC) once a Prefect server is configured.
    score_universe.serve(
        name="score-universe-nightly", cron="0 22 * * 1-5", parameters={"universe": []}
    )
