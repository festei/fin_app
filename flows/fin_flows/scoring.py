"""Scheduled scoring: score the stored universe, record signals, create and deliver alerts.

All the logic is ``fin_application.scoring_run.RunScoring``; this is the Prefect wrapper that fails
the flow run when the whole run failed (including invalid channel configuration).
"""

from __future__ import annotations

from prefect import flow

from fin_bootstrap import Container
from fin_flows.common import check_summary


def _container() -> Container:  # indirection so tests can inject a container
    return Container.from_environ()


@flow(name="score-universe")
def score_universe_flow(tickers: list[str] | None = None) -> dict:
    container = _container()
    try:
        return check_summary(container.run_scoring.execute(tickers=tickers))
    finally:
        container.dispose()
