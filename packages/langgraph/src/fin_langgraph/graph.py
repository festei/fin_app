"""The scoring graph: START -> [philosophy nodes in parallel] -> guardrails -> aggregate -> END.

Deciding whether the conviction is a *signal* is not part of scoring (it needs the stored
history); that is ``fin_domain.signals.decide_signal``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from fin_domain.scoring import Assessment, SecurityMetrics
from fin_langgraph.nodes import PHILOSOPHY_NODES, aggregate_node, guardrails_node
from fin_langgraph.state import GraphState


def build_graph():
    g = StateGraph(GraphState)
    for name, fn in PHILOSOPHY_NODES.items():
        g.add_node(name, fn)
        g.add_edge(START, name)
    g.add_node("guardrails", guardrails_node)
    g.add_node("aggregate", aggregate_node)
    # Join: guardrails waits for every philosophy node.
    g.add_edge(list(PHILOSOPHY_NODES), "guardrails")
    g.add_edge("guardrails", "aggregate")
    g.add_edge("aggregate", END)
    return g.compile()


class LangGraphScoringEngine:
    """The ``ScoringEngine`` port on top of the compiled graph (built once, reused)."""

    def __init__(self) -> None:
        self._graph = build_graph()

    def assess(self, metrics: SecurityMetrics, config: Mapping[str, Any]) -> Assessment:
        out = self._graph.invoke({"metrics": metrics, "config": dict(config)})
        return Assessment(
            metrics=metrics,
            conviction=out["conviction"],
            all_abstained=bool(out.get("all_abstained", False)),
            explanation=out["explanation"],
            philosophy_results=out["philosophy_results"],
            guardrail_results=out.get("guardrail_results", {}),
            guardrail_reasons=out.get("guardrail_reasons", []),
            guardrail_notes=out.get("guardrail_notes", []),
        )
