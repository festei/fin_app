"""Scheduled scoring: score the stored universe, record signals, create and deliver alerts.

All the logic is in ``fin_core.scoring_run.run_scoring``; this is the Prefect wrapper that fails
the flow run when the whole run failed (including invalid channel configuration).
"""

from __future__ import annotations

from prefect import flow

from fin_core.db.session import make_engine
from fin_core.scoring_run import run_scoring
from fin_flows.common import check_summary


@flow(name="score-universe")
def score_universe_flow(tickers: list[str] | None = None) -> dict:
    return check_summary(run_scoring(make_engine(), tickers=tickers))
