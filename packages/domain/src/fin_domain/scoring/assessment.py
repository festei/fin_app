"""One security, assessed: every philosophy, the guardrails, and the conviction they add up to."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from fin_domain.scoring.aggregate import aggregate
from fin_domain.scoring.guardrails import GuardrailVerdict, assess_guardrails
from fin_domain.scoring.models import ScoreResult, SecurityMetrics
from fin_domain.scoring.philosophies import PHILOSOPHIES, PhilosophyScorer, run_philosophy


@dataclass
class Assessment:
    """The scoring context's answer. ``conviction`` is ``None`` when nothing could be evaluated
    (``all_abstained``): "no view", never a score of 0."""

    metrics: SecurityMetrics
    conviction: float | None
    all_abstained: bool
    explanation: str
    philosophy_results: dict[str, ScoreResult]
    guardrail_results: dict[str, ScoreResult] = field(default_factory=dict)
    guardrail_reasons: list[str] = field(default_factory=list)
    guardrail_notes: list[str] = field(default_factory=list)


def build_assessment(
    metrics: SecurityMetrics,
    philosophy_results: Mapping[str, ScoreResult],
    verdict: GuardrailVerdict,
    config: Mapping[str, Any],
) -> Assessment:
    """The step after the philosophies and guardrails have run, shared by every engine."""
    result = aggregate(philosophy_results, config.get("weights", {}), verdict)
    return Assessment(
        metrics=metrics,
        conviction=result.conviction,
        all_abstained=result.all_abstained,
        explanation=result.explanation,
        philosophy_results=dict(philosophy_results),
        guardrail_results=dict(verdict.results),
        guardrail_reasons=list(verdict.reasons),
        guardrail_notes=list(verdict.notes),
    )


def assess(
    metrics: SecurityMetrics,
    config: Mapping[str, Any],
    scorers: Mapping[str, PhilosophyScorer] | None = None,
) -> Assessment:
    """The reference engine: every philosophy in turn, then the guardrails, then the aggregate.
    Other engines (the LangGraph one runs the philosophies in parallel) must give the same
    answer; a parity test holds them to it."""
    scorers = PHILOSOPHIES if scorers is None else scorers
    results = {name: run_philosophy(name, fn, metrics, config) for name, fn in scorers.items()}
    verdict = assess_guardrails(metrics, config.get("guardrails", {}))
    return build_assessment(metrics, results, verdict, config)
