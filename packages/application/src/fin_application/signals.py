"""Use cases: score securities on a schedule, storing every run and the signals it raises."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from fin_application.ports import (
    Clock,
    ScoringConfigSource,
    ScoringEngine,
    UnitOfWork,
    UnitOfWorkFactory,
)
from fin_application.scoring import (
    LiveScore,
    baseline_from_history,
    load_metrics,
    parse_ticker,
    score,
)
from fin_domain.shared import Ticker
from fin_domain.signals import ScoreEntry, StoredSignal


@dataclass
class Recorded:
    score: ScoreEntry
    signal: StoredSignal | None  # only a signal raised by THIS run
    live: LiveScore


@dataclass
class BatchSummary:
    scored: int = 0
    signals: list[tuple[int, str, str]] = field(default_factory=list)  # (signal_id, ticker, kind)
    failed: dict[str, str] = field(default_factory=dict)


class ScoreUniverse:
    """Score every active stock, one transaction per security, so one failure can neither roll
    back nor block the others. Scoring is idempotent: with unchanged data a re-run reaches the
    same conviction, hence no transition, hence no new signal."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        engine: ScoringEngine,
        config: ScoringConfigSource,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._engine = engine
        self._config = config
        self._clock = clock

    def record(self, uow: UnitOfWork, ticker: Ticker, as_of: date | datetime) -> Recorded:
        """Score one security inside ``uow``: store the run and the signal, if one was raised."""
        baseline = baseline_from_history(uow, ticker)
        metrics = load_metrics(uow, ticker, as_of)
        live = score(metrics, self._engine, self._config.load(), baseline)
        run = ScoreEntry.record(live.assessment, live.explanation)
        run.computed_at = self._clock.now()
        entry = uow.scores.add(run)
        stored = uow.signals.add(ticker, entry.id, live.signal) if live.signal is not None else None
        return Recorded(entry, stored, live)

    def score_one(self, ticker: str, as_of: date | datetime) -> Recorded:
        with self._uow_factory() as uow:
            return self.record(uow, parse_ticker(ticker), as_of)

    def execute(self, as_of: date | datetime, tickers: Sequence[str] | None = None) -> BatchSummary:
        wanted = {Ticker(t) for t in tickers} if tickers else None
        with self._uow_factory() as uow:
            targets = [
                s.ticker
                for s in uow.securities.list()
                if s.has_fundamentals and (wanted is None or s.ticker in wanted)
            ]
        summary = BatchSummary()
        for ticker in targets:
            try:
                with self._uow_factory() as uow:
                    recorded = self.record(uow, ticker, as_of)
                summary.scored += 1
                if recorded.signal is not None:
                    summary.signals.append(
                        (recorded.signal.id, str(ticker), recorded.signal.signal.kind)
                    )
            except Exception as exc:
                summary.failed[str(ticker)] = f"{type(exc).__name__}: {exc}"[:200]
        return summary
