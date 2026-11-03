"""Ports of the Scoring context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from fin_domain.scoring import Assessment, SecurityMetrics


class ScoringEngine(Protocol):
    """Runs the philosophies and guardrails and aggregates them. The domain has a reference
    implementation (``fin_domain.scoring.assess``); the LangGraph adapter runs the philosophies
    in parallel. Both must give the same answer."""

    def assess(self, metrics: SecurityMetrics, config: Mapping[str, Any]) -> Assessment: ...


class ScoringConfigSource(Protocol):
    def load(self) -> Mapping[str, Any]:
        """Thresholds and weights per philosophy, validated."""
        ...
