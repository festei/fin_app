"""Has the price source rewritten history we already hold?"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from fin_domain.market_data.prices import PriceBar

# Relative close difference above which a stored bar counts as restated by the price source.
RESTATEMENT_TOLERANCE = 1e-5


def is_restated(stored_closes: Mapping[datetime, float], fetched: Sequence[PriceBar]) -> bool:
    """True if the source now reports different closes than we stored for the same bars.

    Yahoo's default prices are adjusted for splits and dividends as of fetch time, so after a
    corporate action every earlier bar changes. Overwriting only the recent window would leave
    the older history on the old basis (a discontinuity); the caller re-back-fills instead.
    A bar we do not hold yet is new, not a restatement.
    """
    for bar in fetched:
        old = stored_closes.get(bar.ts)
        if old is not None and abs(bar.close - old) > RESTATEMENT_TOLERANCE * max(abs(old), 1e-9):
            return True
    return False
