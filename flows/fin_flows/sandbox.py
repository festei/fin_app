"""Scheduled sandbox step: strategy portfolios act on new signals, pending orders fill at the
next daily close, and every portfolio's value is snapshotted. All logic is in
``fin_core.sandbox.run``."""

from __future__ import annotations

from prefect import flow

from fin_core.db.session import make_engine
from fin_core.sandbox.run import run_sandbox
from fin_flows.common import check_summary


@flow(name="sandbox-step")
def sandbox_step_flow() -> dict:
    return check_summary(run_sandbox(make_engine()))
