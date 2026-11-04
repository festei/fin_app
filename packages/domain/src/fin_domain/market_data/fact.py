"""A reported fundamental figure, exactly as filed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fin_domain.shared import InvariantViolation


@dataclass(frozen=True)
class Fact:
    """One filed version of one figure. Restatements are separate facts with a later ``filed``.

    Instant facts (balance-sheet items) have ``period_start == period_end``, which keeps the
    natural key free of missing values.
    """

    concept: str
    tag: str
    unit: str
    period_start: date
    period_end: date
    instant: bool
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    filed: date
    accession: str
    value: Decimal
    # Order of insertion; breaks ties between two versions filed on the same day (later wins).
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.period_end < self.period_start:
            raise InvariantViolation(f"{self.tag}: the period ends before it starts")
        if self.instant and self.period_start != self.period_end:
            raise InvariantViolation(f"{self.tag}: an instant fact must have a zero-length period")
        if not self.value.is_finite():
            raise InvariantViolation(f"{self.tag}: the value is not a finite number")
