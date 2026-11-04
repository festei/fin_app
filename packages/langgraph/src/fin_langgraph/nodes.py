"""LangGraph nodes: thin wrappers that read the graph state, call the domain, write the result.
The rules are in ``fin_domain.scoring``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fin_domain.scoring import (
    PHILOSOPHIES,
    GuardrailVerdict,
    PhilosophyScorer,
    aggregate,
    assess_guardrails,
    run_philosophy,
)

Node = Callable[[dict[str, Any]], dict[str, Any]]


def philosophy_node(name: str, scorer: PhilosophyScorer) -> Node:
    def node(state: dict[str, Any]) -> dict[str, Any]:
        result = run_philosophy(name, scorer, state["metrics"], state["config"])
        return {"philosophy_results": {name: result}}

    node.__name__ = f"{name}_node"
    return node


PHILOSOPHY_NODES: dict[str, Node] = {
    name: philosophy_node(name, scorer) for name, scorer in PHILOSOPHIES.items()
}


def guardrails_node(state: dict[str, Any]) -> dict[str, Any]:
    verdict = assess_guardrails(state["metrics"], state["config"].get("guardrails", {}))
    return {
        "guardrail_veto": verdict.veto,
        "guardrail_results": verdict.results,
        "guardrail_reasons": verdict.reasons,
        "guardrail_notes": verdict.notes,
    }


def aggregate_node(state: dict[str, Any]) -> dict[str, Any]:
    verdict = GuardrailVerdict(
        veto=state.get("guardrail_veto", False),
        results=state.get("guardrail_results", {}),
        reasons=state.get("guardrail_reasons", []),
        notes=state.get("guardrail_notes", []),
    )
    result = aggregate(state["philosophy_results"], state["config"].get("weights", {}), verdict)
    return {
        "conviction": result.conviction,
        "all_abstained": result.all_abstained,
        "explanation": result.explanation,
    }
