from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fin_domain.scoring import Assessment, ScoreResult, SecurityMetrics


class FakeScoringEngine:
    """Returns a scripted conviction (or ``None`` for "nothing could be evaluated") and records
    what it was asked to assess."""

    def __init__(self, conviction: float | None = 50.0):
        self.conviction = conviction
        self.calls: list[tuple[SecurityMetrics, Mapping[str, Any]]] = []

    def assess(self, metrics: SecurityMetrics, config: Mapping[str, Any]) -> Assessment:
        self.calls.append((metrics, config))
        return Assessment(
            metrics=metrics,
            conviction=self.conviction,
            all_abstained=self.conviction is None,
            explanation="scripted",
            philosophy_results={
                "stub": ScoreResult(
                    "stub", self.conviction or 0.0, evaluated=self.conviction is not None
                )
            },
        )


class FakeScoringConfig:
    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = config or {"weights": {}}

    def load(self) -> Mapping[str, Any]:
        return self.config
