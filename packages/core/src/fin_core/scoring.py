"""Score one stored security: point-in-time metrics -> philosophy graph."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from fin_core.graph import build_graph, load_config
from fin_core.metrics import compute_metrics


def score_security(
    session: Session,
    ticker: str,
    as_of: date | datetime,
    *,
    previous_conviction: float | None = None,
    buy_armed: bool = True,
    sell_armed: bool = True,
    graph: Any = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the graph on metrics computed from the database. Returns the graph state, which
    includes the ``metrics`` that were scored (with their data-quality ``warnings``)."""
    metrics = compute_metrics(session, ticker, as_of)
    graph = graph or build_graph()
    return graph.invoke(
        {
            "metrics": metrics,
            "config": config or load_config(),
            "previous_conviction": previous_conviction,
            "buy_armed": buy_armed,
            "sell_armed": sell_armed,
        }
    )
