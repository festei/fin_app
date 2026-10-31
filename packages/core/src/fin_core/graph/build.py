"""Builds the LangGraph scoring graph.

START -> [philosophy nodes in parallel] -> guardrails -> aggregate -> decide -> END
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from langgraph.graph import END, START, StateGraph

from fin_core.graph.guardrails import guardrails_node
from fin_core.graph.nodes import PHILOSOPHY_NODES
from fin_core.graph.state import GraphState
from fin_core.models import SecurityMetrics, Signal

BUY_THRESHOLD = 70.0
SELL_THRESHOLD = 50.0


def load_config(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        for parent in Path(__file__).resolve().parents:
            if (parent / "config" / "philosophies.yml").exists():
                path = parent / "config" / "philosophies.yml"
                break
    if path is None:
        raise FileNotFoundError("config/philosophies.yml not found")
    return yaml.safe_load(path.read_text())


def aggregate_node(state: dict[str, Any]) -> dict[str, Any]:
    """Weighted mean of the philosophies that could be evaluated. Abstentions (too little data)
    are excluded from the weighting rather than counted as zero, and are named in the text."""
    all_results = state["philosophy_results"]
    weights = state["config"].get("weights", {})
    results = {n: r for n, r in all_results.items() if r.evaluated}
    abstained = sorted(n for n, r in all_results.items() if not r.evaluated)
    note = f" Not evaluated (insufficient data): {', '.join(abstained)}." if abstained else ""
    total = sum(weights.get(name, 1.0) for name in results)
    if not results or total <= 0:
        # None, not 0.0: "no view" must not be stored as previous_conviction or read as a collapse
        return {
            "conviction": None,
            "all_abstained": True,
            "explanation": f"No philosophy could be evaluated.{note}",
        }
    conviction = sum(r.score * weights.get(n, 1.0) for n, r in results.items()) / total
    capped = ""
    if state.get("guardrail_veto"):
        conviction = min(conviction, SELL_THRESHOLD - 1)
        capped = f" Capped by guardrails: {'; '.join(state.get('guardrail_reasons', []))}."
    notes = state.get("guardrail_notes", [])
    if notes:
        capped += f" Guardrail notes: {'; '.join(notes)}."
    parts = ", ".join(f"{n} {r.score:.0f}" for n, r in sorted(results.items()))
    return {
        "conviction": round(conviction, 1),
        "all_abstained": False,
        "explanation": f"Conviction from: {parts}.{note}{capped}",
    }


def decide_node(state: dict[str, Any]) -> dict[str, Any]:
    """Emit a signal only on a transition across a threshold, never on every run."""
    if state.get("all_abstained"):
        return {"signal": None}  # nothing was evaluated: a 0 here is absence of data, not a view
    now: float = state["conviction"]
    prev: float | None = state.get("previous_conviction")
    m: SecurityMetrics = state["metrics"]
    kind = None
    if now >= BUY_THRESHOLD and (prev is None or prev < BUY_THRESHOLD):
        kind = "emerging" if prev is None else "buy"
    elif prev is not None and prev >= SELL_THRESHOLD and now < SELL_THRESHOLD:
        kind = "sell"
    blockers = list(m.signal_blockers)
    if blockers:
        # Stale or missing price/fundamentals must not produce a buy/sell: say why instead.
        note = f"Signal withheld: {'; '.join(blockers)}."
        return {"signal": None, "explanation": f"{state.get('explanation', '')} {note}".strip()}
    signal = (
        Signal(
            ticker=m.ticker,
            kind=kind,
            conviction=now,
            as_of=m.as_of if isinstance(m.as_of, datetime) else datetime.now(),
            reason=state.get("explanation", ""),
        )
        if kind
        else None
    )
    return {"signal": signal}


def build_graph():
    g = StateGraph(GraphState)
    for name, fn in PHILOSOPHY_NODES.items():
        g.add_node(name, fn)
        g.add_edge(START, name)
    g.add_node("guardrails", guardrails_node)
    g.add_node("aggregate", aggregate_node)
    g.add_node("decide", decide_node)
    # Join: guardrails waits for every philosophy node.
    g.add_edge(list(PHILOSOPHY_NODES), "guardrails")
    g.add_edge("guardrails", "aggregate")
    g.add_edge("aggregate", "decide")
    g.add_edge("decide", END)
    return g.compile()
