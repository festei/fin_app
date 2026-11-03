"""Stored scoring runs, and the rules that read them.

Every scoring run is stored, append-only. Two rules decide what a later run compares itself
against, and both used to live inside SQL:

* the *baseline* is the last **actionable** score: conviction set AND not computed on blocked
  (stale or missing) data. A blocked run is stored but never becomes the baseline, otherwise the
  buy would be swallowed once fresh data arrives; a run where nothing could be evaluated (no
  conviction) is not a baseline either, so the next real score is ``emerging``, not ``buy``;
* *hysteresis*: after a buy/emerging signal another buy needs conviction to have fallen below
  ``BUY_REARM_BELOW`` since; after a sell another sell needs it back at ``SELL_REARM_ABOVE``.
  Only actionable scores count. With no earlier signal of that kind the side is armed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from fin_domain.scoring import Assessment, ScoreResult
from fin_domain.shared import Ticker
from fin_domain.signals.decider import BUY_REARM_BELOW, SELL_REARM_ABOVE, Signal

BUY_KINDS = ("buy", "emerging")
SELL_KINDS = ("sell",)
MAX_SUPPRESSED_REASON = 200


@dataclass(frozen=True)
class MetricsSummary:
    """The few metrics worth keeping next to a stored score."""

    price: float | None = None
    price_date: date | None = None
    pe: float | None = None
    pe_avg3: float | None = None
    pb: float | None = None
    latest_fiscal_year_end: date | None = None


@dataclass(frozen=True)
class ScoreDetails:
    """What a stored score remembers about how it came about: the typed form of the JSON blob
    ``scores.details``."""

    philosophies: Mapping[str, ScoreResult] = field(default_factory=dict)
    guardrails: Mapping[str, ScoreResult] = field(default_factory=dict)
    guardrail_reasons: tuple[str, ...] = ()
    guardrail_notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    signal_blockers: tuple[str, ...] = ()
    metrics: MetricsSummary = field(default_factory=MetricsSummary)

    @classmethod
    def of(cls, a: Assessment) -> ScoreDetails:
        m = a.metrics
        return cls(
            philosophies=dict(a.philosophy_results),
            guardrails=dict(a.guardrail_results),
            guardrail_reasons=tuple(a.guardrail_reasons),
            guardrail_notes=tuple(a.guardrail_notes),
            warnings=tuple(m.warnings),
            signal_blockers=tuple(m.signal_blockers),
            metrics=MetricsSummary(
                price=m.price,
                price_date=m.price_date,
                pe=m.pe,
                pe_avg3=m.pe_avg3,
                pb=m.pb,
                latest_fiscal_year_end=m.latest_fiscal_year_end,
            ),
        )

    def evaluated(self) -> dict[str, ScoreResult]:
        return {n: r for n, r in self.philosophies.items() if r.evaluated}


def is_actionable(conviction: float | None, signal_blocked: bool) -> bool:
    return conviction is not None and not signal_blocked


@dataclass
class ScoreEntry:
    """One stored scoring run. ``conviction`` is ``None`` when nothing could be evaluated."""

    ticker: Ticker
    as_of: datetime
    conviction: float | None
    all_abstained: bool
    signal_blocked: bool
    explanation: str
    details: ScoreDetails = field(default_factory=ScoreDetails)
    id: int | None = None
    computed_at: datetime | None = None

    @property
    def actionable(self) -> bool:
        return is_actionable(self.conviction, self.signal_blocked)

    @classmethod
    def record(cls, assessment: Assessment, explanation: str) -> ScoreEntry:
        """A run to store. ``explanation`` is the final one: the assessment's, plus why a signal
        was withheld, if it was."""
        m = assessment.metrics
        return cls(
            ticker=Ticker(m.ticker),
            as_of=m.as_of,
            conviction=assessment.conviction,
            all_abstained=assessment.all_abstained,
            signal_blocked=bool(m.signal_blockers),
            explanation=explanation,
            details=ScoreDetails.of(assessment),
        )


def armed_after(
    convictions_since_last_signal: Sequence[float] | None, moved_away: Callable[[float], bool]
) -> bool:
    """``None`` means there has been no signal of that kind: armed. Otherwise armed once any
    actionable conviction since the last one has moved away from the line."""
    if convictions_since_last_signal is None:
        return True
    return any(moved_away(c) for c in convictions_since_last_signal)


def buy_armed(convictions_since_last_buy: Sequence[float] | None) -> bool:
    return armed_after(convictions_since_last_buy, lambda c: c < BUY_REARM_BELOW)


def sell_armed(convictions_since_last_sell: Sequence[float] | None) -> bool:
    return armed_after(convictions_since_last_sell, lambda c: c >= SELL_REARM_ABOVE)


@dataclass
class StoredSignal:
    """A signal as stored, with the state of its alert decision. A signal starts *pending*; the
    alert decision marks it processed (with a reason if it was deliberately not alerted), so an
    interrupted run can finish exactly the ones still owed."""

    ticker: Ticker
    signal: Signal
    score_id: int
    id: int | None = None
    created_at: datetime | None = None
    processed_at: datetime | None = None
    suppressed_reason: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.processed_at is None

    def mark_processed(self, now: datetime, suppressed_reason: str | None = None) -> None:
        self.processed_at = now
        self.suppressed_reason = (suppressed_reason or None) and suppressed_reason[
            :MAX_SUPPRESSED_REASON
        ]
