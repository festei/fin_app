"""Domain events: facts that happened, named in the past tense.

Aggregates record them; the application layer decides what to do with them. They are plain
values, so they carry no behaviour and no framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fin_domain.shared.time import ensure_utc


@dataclass(frozen=True)
class DomainEvent:
    occurred_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at, "occurred_at"))
