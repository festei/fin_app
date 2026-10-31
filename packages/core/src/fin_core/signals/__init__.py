"""Signal engine: turn scoring runs into persisted state and transitions."""

from fin_core.signals.engine import BatchSummary, ScoreOutcome, score_and_record, score_universe

__all__ = ["BatchSummary", "ScoreOutcome", "score_and_record", "score_universe"]
