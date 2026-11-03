"""Turn the philosophies' results and the guardrail verdict into one conviction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from fin_domain.scoring.guardrails import GuardrailVerdict
from fin_domain.scoring.models import ScoreResult

# A guardrail veto caps conviction here: just below the sell threshold of the signals context
# (``fin_domain.signals.SELL_THRESHOLD``, 50), so a vetoed company can never read as a buy. A test
# in the signals context pins the two together.
VETO_CAP = 49.0


@dataclass(frozen=True)
class Aggregation:
    # None, not 0.0: "no view" must not be stored as a previous conviction or read as a collapse
    conviction: float | None
    all_abstained: bool
    explanation: str


def aggregate(
    all_results: Mapping[str, ScoreResult], weights: Mapping[str, float], verdict: GuardrailVerdict
) -> Aggregation:
    """Weighted mean of the philosophies that could be evaluated. Abstentions (too little data)
    are excluded from the weighting rather than counted as zero, and are named in the text."""
    results = {n: r for n, r in all_results.items() if r.evaluated}
    abstained = sorted(n for n, r in all_results.items() if not r.evaluated)
    note = f" Not evaluated (insufficient data): {', '.join(abstained)}." if abstained else ""
    total = sum(weights.get(name, 1.0) for name in results)
    if not results or total <= 0:
        return Aggregation(None, True, f"No philosophy could be evaluated.{note}")
    conviction = sum(r.score * weights.get(n, 1.0) for n, r in results.items()) / total
    capped = ""
    if verdict.veto:
        conviction = min(conviction, VETO_CAP)
        capped = f" Capped by guardrails: {'; '.join(verdict.reasons)}."
    if verdict.notes:
        capped += f" Guardrail notes: {'; '.join(verdict.notes)}."
    parts = ", ".join(f"{n} {r.score:.0f}" for n, r in sorted(results.items()))
    return Aggregation(round(conviction, 1), False, f"Conviction from: {parts}.{note}{capped}")
