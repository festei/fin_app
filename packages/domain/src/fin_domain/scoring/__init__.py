"""Scoring context: judge a security against investment philosophies. Stateless: the same
metrics and thresholds always give the same assessment."""

from fin_domain.scoring.aggregate import VETO_CAP, Aggregation, aggregate
from fin_domain.scoring.assessment import Assessment, assess, build_assessment
from fin_domain.scoring.guardrails import ALTMAN, PIOTROSKI, GuardrailVerdict, assess_guardrails
from fin_domain.scoring.metrics import (
    MULTI_CLASS_TICKERS,
    average_eps_3y,
    calculate_metrics,
    consecutive_positive_years,
    earnings_growth,
    resolve_as_of,
)
from fin_domain.scoring.models import AnnualPoint, Criterion, ScoreResult, SecurityMetrics
from fin_domain.scoring.philosophies import (
    PHILOSOPHIES,
    PhilosophyScorer,
    run_philosophy,
    validate_config,
)

__all__ = [
    "ALTMAN",
    "MULTI_CLASS_TICKERS",
    "PHILOSOPHIES",
    "PIOTROSKI",
    "VETO_CAP",
    "Aggregation",
    "AnnualPoint",
    "Assessment",
    "Criterion",
    "GuardrailVerdict",
    "PhilosophyScorer",
    "ScoreResult",
    "SecurityMetrics",
    "aggregate",
    "assess",
    "assess_guardrails",
    "average_eps_3y",
    "build_assessment",
    "calculate_metrics",
    "consecutive_positive_years",
    "earnings_growth",
    "resolve_as_of",
    "run_philosophy",
    "validate_config",
]
