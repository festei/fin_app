"""The philosophies: pure functions ``(SecurityMetrics, thresholds) -> ScoreResult``.

To add one: a module here, an entry in ``PHILOSOPHIES``, its thresholds and a weight in
``config/philosophies.yml``. ``validate_config`` keeps the registry and the config in step.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fin_domain.scoring.models import ScoreResult, SecurityMetrics
from fin_domain.scoring.philosophies.buffett import NAME as BUFFETT
from fin_domain.scoring.philosophies.buffett import score_buffett
from fin_domain.scoring.philosophies.fisher import NAME as FISHER
from fin_domain.scoring.philosophies.fisher import score_fisher
from fin_domain.scoring.philosophies.graham import NAME as GRAHAM
from fin_domain.scoring.philosophies.graham import score_graham
from fin_domain.scoring.philosophies.greenblatt import NAME as GREENBLATT
from fin_domain.scoring.philosophies.greenblatt import score_greenblatt
from fin_domain.scoring.philosophies.lynch import NAME as LYNCH
from fin_domain.scoring.philosophies.lynch import score_lynch
from fin_domain.shared import InvariantViolation

PhilosophyScorer = Callable[[SecurityMetrics, Mapping[str, Any]], ScoreResult]

PHILOSOPHIES: dict[str, PhilosophyScorer] = {
    GRAHAM: score_graham,
    BUFFETT: score_buffett,
    LYNCH: score_lynch,
    GREENBLATT: score_greenblatt,
    FISHER: score_fisher,
}


def run_philosophy(
    name: str, scorer: PhilosophyScorer, metrics: SecurityMetrics, config: Mapping[str, Any]
) -> ScoreResult:
    """Score with one philosophy using its section of ``config``. ``impl`` selects the
    implementation: deterministic today, an LLM agent once those exist."""
    section = config[name]
    if section.get("impl", "deterministic") != "deterministic":
        raise NotImplementedError(f"LLM-backed {name.capitalize()} node is not implemented yet")
    return scorer(metrics, section)


def validate_config(config: Mapping[str, Any]) -> None:
    """Every philosophy needs a section and a weight, and nothing else may have one."""
    weights = set(config.get("weights", {}))
    if weights != set(PHILOSOPHIES):
        raise InvariantViolation(
            f"weights {sorted(weights)} do not match the philosophies {sorted(PHILOSOPHIES)}"
        )
    missing = [n for n in PHILOSOPHIES if n not in config]
    if missing:
        raise InvariantViolation(f"no thresholds configured for {missing}")
