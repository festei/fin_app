"""The values the scoring context works with.

``ScoreResult`` is the single contract every philosophy must satisfy, whether it is a
deterministic function today or an LLM agent later. These are plain dataclasses: the HTTP layer
validates and serialises them (pydantic reads standard dataclasses), the domain needs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from fin_domain.shared import InvariantViolation


@dataclass
class Criterion:
    """One pass/fail test inside a philosophy, with the actual number shown."""

    name: str
    passed: bool
    actual: float | None = None
    threshold: float | None = None
    detail: str = ""


@dataclass
class ScoreResult:
    philosophy: str
    score: float
    criteria: list[Criterion] = field(default_factory=list)
    explanation: str = ""
    evidence: list[str] = field(default_factory=list)
    impl: Literal["deterministic", "llm"] = "deterministic"
    # False = this philosophy could not be evaluated (e.g. too little history). It abstains: the
    # aggregate ignores it instead of counting a 0, and `score` carries no meaning.
    evaluated: bool = True

    def __post_init__(self) -> None:
        self.score = float(self.score)
        if not 0 <= self.score <= 100:
            raise InvariantViolation(f"score must be between 0 and 100, got {self.score}")

    @property
    def passed_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.passed]

    @property
    def failed_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if not c.passed]


@dataclass
class AnnualPoint:
    period_end: date
    value: float


@dataclass
class SecurityMetrics:
    """Point-in-time metrics for one security. Missing values are ``None``.

    The scalar ratios are what the Graham node uses; ``annual`` carries the fiscal-year history
    (oldest first) that the multi-year scorers (Buffett, Piotroski, Altman) work from.
    """

    ticker: str
    as_of: datetime
    price: float | None = None
    price_date: date | None = None
    pe: float | None = None  # price / latest fiscal-year diluted EPS
    pe_avg3: float | None = None  # price / average of the latest 3 fiscal years (Graham's P/E)
    pb: float | None = None
    current_ratio: float | None = None
    debt_to_equity: float | None = None
    roe: float | None = None
    earnings_growth: float | None = None
    dividend_years: int | None = None
    eps: float | None = None
    book_value_per_share: float | None = None
    latest_fiscal_year_end: date | None = None
    sector: str | None = None
    # Share counts and per-share values cover one class only (see MULTI_CLASS_TICKERS).
    multi_class: bool = False
    annual: dict[str, list[AnnualPoint]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)  # data-quality notes, never silent gaps
    # Subset of the warnings that make the data too poor to emit a buy/sell signal on
    # (no or stale price/fundamentals). The decision to signal withholds while this is set.
    signal_blockers: list[str] = field(default_factory=list)
