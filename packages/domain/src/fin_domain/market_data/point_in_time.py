"""What was public on a given day."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from fin_domain.market_data.fact import Fact


def latest_versions(facts: Iterable[Fact]) -> list[Fact]:
    """For every (tag, unit, period) keep only the latest filed version (a tie on the filing
    day goes to the one stored last). Ordered by concept, then period end."""
    best: dict[tuple, Fact] = {}
    for f in facts:
        key = (f.tag, f.unit, f.period_start, f.period_end)
        held = best.get(key)
        if held is None or (f.filed, f.sequence) > (held.filed, held.sequence):
            best[key] = f
    return sorted(best.values(), key=lambda f: (f.concept, f.period_end))


def known_as_of(facts: Iterable[Fact], as_of: date) -> list[Fact]:
    """The point-in-time view: the latest version of each figure FILED on or before ``as_of``.
    A restatement filed after ``as_of`` is invisible, so there is no look-ahead."""
    return latest_versions(f for f in facts if f.filed <= as_of)
