"""The nightly sandbox step: strategies read new signals, orders settle, values are snapshotted.

All the logic is ``fin_application.sandbox.RunSandbox``; this is the Prefect wrapper that fails the
flow run when the whole run failed.
"""

from __future__ import annotations

from prefect import flow

from fin_bootstrap import Container
from fin_flows.common import check_summary


def _container() -> Container:  # indirection so tests can inject a container
    return Container.from_environ()


@flow(name="sandbox-step")
def sandbox_step_flow() -> dict:
    container = _container()
    try:
        return check_summary(container.run_sandbox.execute())
    finally:
        container.dispose()
