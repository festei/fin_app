from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from fin_domain.scoring import Assessment, ScoreResult, SecurityMetrics


class FakeScoringEngine:
    """Returns a scripted conviction (or ``None`` for "nothing could be evaluated") and records
    what it was asked to assess."""

    def __init__(self, conviction: float | None = 50.0, *, raise_for: Collection[str] = ()):
        self.conviction = conviction
        self.raise_for = set(raise_for)  # tickers whose assessment crashes
        self.per_ticker: dict[str, float | None] = {}  # overrides ``conviction`` per ticker
        self.calls: list[tuple[SecurityMetrics, Mapping[str, Any]]] = []

    def assess(self, metrics: SecurityMetrics, config: Mapping[str, Any]) -> Assessment:
        self.calls.append((metrics, config))
        if metrics.ticker in self.raise_for:
            raise RuntimeError(f"engine crashed on {metrics.ticker}")
        conviction = self.per_ticker.get(metrics.ticker, self.conviction)
        return Assessment(
            metrics=metrics,
            conviction=conviction,
            all_abstained=conviction is None,
            explanation="scripted",
            philosophy_results={
                "stub": ScoreResult("stub", conviction or 0.0, evaluated=conviction is not None)
            },
        )


class FakeScoringConfig:
    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = config or {"weights": {}}

    def load(self) -> Mapping[str, Any]:
        return self.config
