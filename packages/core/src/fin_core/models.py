"""Core data contracts shared by the graph, API, sandbox and UI.

``ScoreResult`` is the single contract every philosophy node must satisfy,
whether it is a deterministic function today or an LLM agent later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Criterion(BaseModel):
    """One pass/fail test inside a philosophy, with the actual number shown."""

    name: str
    passed: bool
    actual: float | None = None
    threshold: float | None = None
    detail: str = ""


class ScoreResult(BaseModel):
    philosophy: str
    score: float = Field(ge=0, le=100)
    criteria: list[Criterion] = Field(default_factory=list)
    explanation: str = ""
    evidence: list[str] = Field(default_factory=list)
    impl: Literal["deterministic", "llm"] = "deterministic"

    @property
    def passed_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.passed]

    @property
    def failed_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if not c.passed]


class SecurityMetrics(BaseModel):
    """Point-in-time metrics for one security. Missing values are ``None``."""

    ticker: str
    as_of: datetime
    price: float | None = None
    pe: float | None = None
    pb: float | None = None
    current_ratio: float | None = None
    debt_to_equity: float | None = None
    roe: float | None = None
    earnings_growth: float | None = None
    dividend_years: int | None = None
    eps: float | None = None
    book_value_per_share: float | None = None


SignalKind = Literal["buy", "sell", "watch", "emerging"]


class Signal(BaseModel):
    ticker: str
    kind: SignalKind
    conviction: float
    as_of: datetime
    reason: str = ""
